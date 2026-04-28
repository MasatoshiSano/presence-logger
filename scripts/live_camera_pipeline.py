#!/usr/bin/env python3
"""Real camera → real inference → real FSM → real MQTT → real Oracle pipeline.

Replaces only the MediaPipe inference layer with OpenCV's built-in HOG person
detector (mediapipe wheels are absent for aarch64+py3.13 on this host). Every
other layer is the production code.

Usage:
  .venv/bin/python scripts/live_camera_pipeline.py                # auto-stop after EXIT
  .venv/bin/python scripts/live_camera_pipeline.py --persistent   # keep running until Ctrl+C

Stand in front of the USB camera at /dev/video0 — after ~3 sec of continuous
detection, an ENTER row appears in HF1RCM01 (Cloud ADB). Step out — after ~3 sec,
an EXIT row appears. Both rows are tagged STA_NO=(TST,T,00) so cleanup deletes
only test markers.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import signal
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2
import paho.mqtt.client as paho
from amqtt.broker import Broker

from services.bridge.src.circuit_breaker import CircuitBreaker
from services.bridge.src.inbox import InboxEvent, InboxRepository
from services.bridge.src.mqtt_listener import BridgeMqttClient, EventPayload
from services.bridge.src.oracle_client import execute_merge, open_connection
from services.bridge.src.profile_resolver import ProfileResolver
from services.bridge.src.retry import BackoffPolicy
from services.bridge.src.sender import Sender, SenderDeps
from services.detector.src.fsm import FSMConfig, Observation, PresenceFSM
from services.detector.src.mqtt_client import DetectorMqttClient

WORK = Path("/tmp/presence-live-camera")
shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(parents=True, exist_ok=True)

PROFILE_NAME = "live_camera_test"
DEVICE_ID = "rpi-camera-test"
PROFILES = {
    PROFILE_NAME: {
        "description": "live ADB",
        "sntp": {"servers": ["ntp.nict.jp"]},
        "oracle": {
            "client_mode": "thin",
            "auth_mode": "wallet",
            "dsn": "eqstatusdb_low",
            "user": "ADMIN",
            "password": "***REDACTED***",
            "wallet_dir": "/home/pi/oracle_wallet",
            "wallet_password": "***REDACTED***",
            "table_name": "HF1RCM01",
        },
    }
}
DEVICE_CFG = {
    "device_id": DEVICE_ID,
    "station": {"sta_no1": "TST", "sta_no2": "T", "sta_no3": "00"},
}


# --- HOG-based PersonDetector (drop-in replacement for MediaPipe inference) ------

class HogPersonDetector:
    """OpenCV's built-in HOG + SVM person detector. No model file needed."""

    def __init__(self):
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def detect_present(self, frame_bgr) -> tuple[bool, float]:
        # detectMultiScale returns (rects, weights). weight ≈ confidence.
        # Downscale for performance on Pi 5 (640x480 → 320x240).
        small = cv2.resize(frame_bgr, (0, 0), fx=0.5, fy=0.5)
        rects, weights = self._hog.detectMultiScale(
            small,
            winStride=(8, 8),
            padding=(8, 8),
            scale=1.05,
            useMeanshiftGrouping=False,
        )
        if len(rects) == 0:
            return False, 0.0
        top_score = float(max(weights)) if len(weights) else 0.0
        return top_score > 0.5, top_score


# --- amqtt broker ----------------------------------------------------------------

BROKER_CONFIG = {
    "listeners": {"default": {"type": "tcp", "bind": "127.0.0.1:1883"}},
    "auth": {"allow-anonymous": True},
    "topic-check": {"enabled": False},
    "sys_interval": 0,
}


async def _run_broker(stop_event: asyncio.Event) -> None:
    broker = Broker(BROKER_CONFIG)
    await broker.start()
    try:
        await stop_event.wait()
    finally:
        await broker.shutdown()


def start_broker_thread():
    loop = asyncio.new_event_loop()
    stop_event = asyncio.Event()

    def _main():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_broker(stop_event))
        loop.close()

    t = threading.Thread(target=_main, daemon=True)
    t.start()
    deadline = time.monotonic() + 10.0
    import socket as _s
    while time.monotonic() < deadline:
        try:
            with _s.create_connection(("127.0.0.1", 1883), timeout=0.5):
                return t, stop_event, loop
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("amqtt broker failed to start within 10s")


# --- Oracle adapter --------------------------------------------------------------

class OracleAdapter:
    def execute_merge_for_profile(self, *, profile, mk_date, sta_no1, sta_no2, sta_no3, t1_status):
        cfg = profile["oracle"]
        conn = open_connection(cfg)
        try:
            return execute_merge(
                conn, table_name=cfg["table_name"],
                mk_date=mk_date, sta_no1=sta_no1, sta_no2=sta_no2,
                sta_no3=sta_no3, t1_status=t1_status,
            )
        finally:
            conn.close()


class FakeNetwork:
    cached_ssid = PROFILE_NAME
    def get_current_ssid(self):
        return PROFILE_NAME


class FakeTimeWatcher:
    is_synced = True
    baseline = None


# --- Main pipeline ---------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--persistent", action="store_true",
                        help="Keep running until Ctrl+C (default: stop after one ENTER+EXIT)")
    parser.add_argument("--camera", type=int, default=0, help="V4L2 camera index")
    parser.add_argument("--debounce", type=float, default=3.0, help="Debounce seconds")
    parser.add_argument("--fps", type=float, default=2.0, help="Inference FPS target")
    args = parser.parse_args()

    print(f"[1/6] Starting amqtt broker on 127.0.0.1:1883")
    t_broker, stop_event, loop = start_broker_thread()
    print(f"      broker up\n")

    print(f"[2/6] Wiring bridge components against live Cloud ADB ({PROFILE_NAME})")
    inbox = InboxRepository(WORK / "inbox.db"); inbox.init()
    resolver = ProfileResolver(profiles=PROFILES, unknown_policy="hold")
    breaker = CircuitBreaker(half_open_after_seconds=900,
                             permanent_codes={942, 904, 1017, 1031, 12514})
    oracle = OracleAdapter()

    bridge_mqtt = BridgeMqttClient(client_id="presence-bridge-camera-live")
    bridge_mqtt.connect_and_loop(host="127.0.0.1", port=1883)

    received: list[str] = []
    acks: list[str] = []

    def on_event(payload: EventPayload, raw: bytes) -> None:
        evt = InboxEvent(
            event_id=payload.event_id, event_type=payload.event_type,
            mk_date=payload.mk_date, monotonic_ns=payload.monotonic_ns,
            wall_synced=payload.wall_clock_synced,
            device_id=payload.device_id, score=payload.score,
            raw_payload=raw.decode("utf-8"), status="received",
            ssid_at_receive=PROFILE_NAME, profile_at_send=None,
            mk_date_committed=None,
            received_at_iso=datetime.now(UTC).isoformat(),
            sent_at_iso=None, retry_count=0, next_retry_at_iso=None, last_error=None,
        )
        inbox.insert_received(evt)
        received.append(payload.event_id)
        print(f"      bridge ⬅ received: event_id={payload.event_id[:8]}... type={payload.event_type}")

    bridge_mqtt.subscribe_event("presence/event", on_event)

    sender = Sender(deps=SenderDeps(
        inbox=inbox, resolver=resolver, breaker=breaker,
        network=FakeNetwork(), time_watcher=FakeTimeWatcher(),
        oracle=oracle, mqtt=bridge_mqtt,
        device_cfg=DEVICE_CFG, topic_ack="presence/event/ack",
        backoff_policy=BackoffPolicy(initial=5.0, multiplier=3.0, cap=600.0),
    ))
    print(f"      bridge ready\n")

    print(f"[3/6] Starting detector with HOG inference + USB camera at /dev/video{args.camera}")
    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"      ERROR: could not open camera {args.camera}", file=sys.stderr)
        return 2
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    for _ in range(5):  # warmup
        cap.read()

    hog = HogPersonDetector()
    fsm = PresenceFSM(config=FSMConfig(enter_seconds=args.debounce, exit_seconds=args.debounce))

    det_mqtt = DetectorMqttClient(client_id_prefix="presence-detector-camera-live")
    det_mqtt.connect_and_loop(host="127.0.0.1", port=1883)

    def _on_ack(event_id: str, mk_date_committed: str) -> None:
        acks.append(event_id)
        print(f"      detector ⬅ ACK: event_id={event_id[:8]}... mk_date={mk_date_committed}")

    det_mqtt.subscribe_ack("presence/event/ack", _on_ack)
    time.sleep(0.5)
    print(f"      detector ready (debounce={args.debounce}s, fps_target={args.fps})\n")

    print(f"[4/6] WATCHING. Stand in front of the camera to trigger ENTER.")
    if args.persistent:
        print(f"      Mode: persistent (Ctrl+C to stop)")
    else:
        print(f"      Mode: one ENTER+EXIT cycle then stop")
    print(f"      Frames @ {args.fps} FPS, transitions logged below:\n")

    period = 1.0 / args.fps
    enter_count, exit_count = 0, 0
    running = True

    def _stop(*_a):
        nonlocal running
        running = False
        print("\n      Ctrl+C received, shutting down...")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    last_log_t = time.monotonic()
    frames = 0
    detections = 0

    while running:
        loop_start = time.monotonic()

        ok, frame = cap.read()
        if not ok:
            print("      camera read failed, skipping")
            time.sleep(period); continue

        frames += 1
        present, score = hog.detect_present(frame)
        if present:
            detections += 1

        obs = Observation(present=present, score=score, monotonic_ns=time.monotonic_ns())
        transition = fsm.observe(obs)

        if transition is not None:
            event_id = str(uuid.uuid4())
            now = datetime.now().astimezone()
            mk = now.strftime("%Y%m%d%H%M%S")
            payload = {
                "event_id": event_id,
                "event": transition.event_type,
                "event_time": mk,
                "event_time_iso": now.isoformat(timespec="milliseconds"),
                "monotonic_ns": transition.confirmed_at_monotonic_ns,
                "wall_clock_synced": True,
                "device_id": DEVICE_ID,
                "score": transition.latest_score,
                "schema_version": 1,
            }
            print(f"\n      🟢 FSM transition: {transition.from_state} → {transition.to_state} ({transition.event_type}) score={transition.latest_score:.2f}")
            print(f"         publishing event_id={event_id[:8]}... mk_date={mk}")
            det_mqtt.publish_event("presence/event", payload, qos=2)
            if transition.event_type == "ENTER":
                enter_count += 1
            else:
                exit_count += 1

        # Drain bridge sender (writes to Oracle).
        sender.run_once(now=datetime.now(UTC))

        # Periodic stats line every 5 sec.
        now_t = time.monotonic()
        if now_t - last_log_t >= 5.0:
            print(f"      [stats] frames={frames} detections={detections} "
                  f"state={fsm.state} enter={enter_count} exit={exit_count}")
            frames = 0; detections = 0
            last_log_t = now_t

        # End-of-cycle exit.
        if not args.persistent and enter_count > 0 and exit_count > 0 and len(acks) >= 2:
            print(f"\n[5/6] one ENTER+EXIT cycle complete, both ACKs received")
            running = False
            break

        elapsed = time.monotonic() - loop_start
        if elapsed < period:
            time.sleep(period - elapsed)

    cap.release()

    # Verify rows in Oracle then cleanup.
    print(f"\n[5/6] Verifying rows in live HF1RCM01 (Cloud ADB)")
    conn = open_connection(PROFILES[PROFILE_NAME]["oracle"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS FROM HF1RCM01 "
                "WHERE STA_NO1='TST' AND STA_NO2='T' AND STA_NO3='00' ORDER BY MK_DATE"
            )
            rows = cur.fetchall()
            for r in rows:
                marker = "ENTER" if r[4] == 1 else "EXIT"
                print(f"      ✓ row: MK_DATE={r[0]} STA={r[1]}/{r[2]}/{r[3]} status={r[4]} ({marker})")
            print(f"      total {len(rows)} TST rows in HF1RCM01")

        if rows:
            print(f"\n[6/6] Cleanup: removing TST/T/00 rows")
            with conn.cursor() as cur:
                cur.execute("DELETE FROM HF1RCM01 WHERE STA_NO1='TST' AND STA_NO2='T' AND STA_NO3='00'")
                print(f"      deleted {cur.rowcount} row(s)")
            conn.commit()
        else:
            print(f"\n[6/6] no TST rows to clean up")
    finally:
        conn.close()

    bridge_mqtt.disconnect()
    det_mqtt.disconnect()
    loop.call_soon_threadsafe(stop_event.set)
    t_broker.join(timeout=5)

    print(f"\n=== DONE: enter={enter_count} exit={exit_count} acks={len(acks)} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
