#!/usr/bin/env python3
"""Real camera → real MediaPipe → real FSM → real MQTT → real Oracle pipeline.

Designed to run INSIDE the detector Docker image (where MediaPipe is installed).
Wraps every layer with production code:
- services.detector.src.inference.PersonDetector (MediaPipe ObjectDetector / EfficientDet-Lite0)
- services.detector.src.fsm.PresenceFSM
- services.detector.src.mqtt_client.DetectorMqttClient
- services.bridge.src.mqtt_listener.BridgeMqttClient
- services.bridge.src.sender.Sender
- services.bridge.src.oracle_client (live Cloud ADB via wallet)

Usage (inside detector image with /dev/video0 + /home/pi/oracle_wallet mounted):
  python scripts/live_camera_pipeline_mediapipe.py
"""
from __future__ import annotations

import asyncio
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
from amqtt.broker import Broker

from services.bridge.src.circuit_breaker import CircuitBreaker
from services.bridge.src.inbox import InboxEvent, InboxRepository
from services.bridge.src.mqtt_listener import BridgeMqttClient, EventPayload
from services.bridge.src.oracle_client import execute_merge, open_connection
from services.bridge.src.profile_resolver import ProfileResolver
from services.bridge.src.retry import BackoffPolicy
from services.bridge.src.sender import Sender, SenderDeps
from services.detector.src.fsm import FSMConfig, Observation, PresenceFSM
from services.detector.src.inference import PersonDetector
from services.detector.src.mqtt_client import DetectorMqttClient

WORK = Path("/tmp/presence-mediapipe-live")
shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(parents=True, exist_ok=True)

PROFILE = "live_test"
DEVICE_ID = "rpi-mediapipe-live"
MODEL_PATH = "/opt/models/efficientdet_lite0.tflite"

PROFILES = {
    PROFILE: {
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


# --- amqtt broker (in-process) ---------------------------------------------------

BROKER_CFG = {
    "listeners": {"default": {"type": "tcp", "bind": "127.0.0.1:1883"}},
    "auth": {"allow-anonymous": True},
    "topic-check": {"enabled": False},
    "sys_interval": 0,
}


async def _run_broker(stop):
    b = Broker(BROKER_CFG); await b.start()
    try: await stop.wait()
    finally: await b.shutdown()


def start_broker():
    loop = asyncio.new_event_loop()
    stop = asyncio.Event()
    def _go():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_broker(stop))
        loop.close()
    threading.Thread(target=_go, daemon=True).start()
    import socket as _s
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            with _s.create_connection(("127.0.0.1", 1883), timeout=0.5):
                return loop, stop
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("broker startup timeout")


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
    cached_ssid = PROFILE
    def get_current_ssid(self): return PROFILE


class FakeTime:
    is_synced = True; baseline = None


# --- Pipeline --------------------------------------------------------------------

def main() -> int:
    print("[1/6] amqtt broker on 127.0.0.1:1883")
    loop, stop = start_broker()
    print("      broker up\n")

    print("[2/6] bridge wired against live Cloud ADB")
    inbox = InboxRepository(WORK / "inbox.db"); inbox.init()
    resolver = ProfileResolver(profiles=PROFILES, unknown_policy="hold")
    breaker = CircuitBreaker(half_open_after_seconds=900,
                             permanent_codes={942, 904, 1017, 1031, 12514})
    oracle = OracleAdapter()
    bridge_mqtt = BridgeMqttClient(client_id="presence-bridge-mediapipe")
    bridge_mqtt.connect_and_loop(host="127.0.0.1", port=1883)

    received, acks = [], []
    def on_event(payload: EventPayload, raw: bytes) -> None:
        evt = InboxEvent(
            event_id=payload.event_id, event_type=payload.event_type,
            mk_date=payload.mk_date, monotonic_ns=payload.monotonic_ns,
            wall_synced=payload.wall_clock_synced, device_id=payload.device_id,
            score=payload.score, raw_payload=raw.decode("utf-8"), status="received",
            ssid_at_receive=PROFILE, profile_at_send=None, mk_date_committed=None,
            received_at_iso=datetime.now(UTC).isoformat(),
            sent_at_iso=None, retry_count=0, next_retry_at_iso=None, last_error=None,
        )
        inbox.insert_received(evt)
        received.append(payload.event_id)
        print(f"      bridge ⬅ event {payload.event_id[:8]}.. {payload.event_type}")

    bridge_mqtt.subscribe_event("presence/event", on_event)

    sender = Sender(deps=SenderDeps(
        inbox=inbox, resolver=resolver, breaker=breaker,
        network=FakeNetwork(), time_watcher=FakeTime(), oracle=oracle, mqtt=bridge_mqtt,
        device_cfg=DEVICE_CFG, topic_ack="presence/event/ack",
        backoff_policy=BackoffPolicy(initial=5.0, multiplier=3.0, cap=600.0),
    ))
    print("      ready\n")

    print(f"[3/6] detector with MediaPipe ObjectDetector + USB camera")
    print(f"      loading {MODEL_PATH}...")
    detector = PersonDetector.from_model_path(
        model_path=MODEL_PATH, score_threshold=0.5, target_category="person",
    )
    print("      MediaPipe loaded")

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("      ERROR: cannot open /dev/video0", file=sys.stderr); return 2
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    for _ in range(5): cap.read()
    print("      camera ready @ 640x480\n")

    fsm = PresenceFSM(config=FSMConfig(enter_seconds=3.0, exit_seconds=3.0))
    det_mqtt = DetectorMqttClient(client_id_prefix="presence-detector-mediapipe")
    det_mqtt.connect_and_loop(host="127.0.0.1", port=1883)

    def _on_ack(event_id: str, mk: str) -> None:
        acks.append(event_id)
        print(f"      detector ⬅ ACK {event_id[:8]}.. mk={mk}")

    det_mqtt.subscribe_ack("presence/event/ack", _on_ack)
    time.sleep(0.5)

    print(f"[4/6] WATCHING. Stand in front of the camera!")
    print(f"      debounce=3.0s, fps_target=2.0, run until ENTER+EXIT cycle")
    print(f"      (or 120 s timeout)\n")

    period = 0.5
    enter_count = exit_count = 0
    deadline = time.monotonic() + 120.0
    last_log = time.monotonic()
    frames = detections = 0
    running = True

    def _stop(*_a):
        nonlocal running; running = False
    signal.signal(signal.SIGINT, _stop); signal.signal(signal.SIGTERM, _stop)

    while running and time.monotonic() < deadline:
        t0 = time.monotonic()
        ok, frame = cap.read()
        if not ok: time.sleep(period); continue
        frames += 1

        r = detector.detect(frame)
        if r.has_person: detections += 1

        obs = Observation(present=r.has_person, score=r.top_score, monotonic_ns=time.monotonic_ns())
        transition = fsm.observe(obs)

        if transition is not None:
            event_id = str(uuid.uuid4())
            now = datetime.now().astimezone()
            mk = now.strftime("%Y%m%d%H%M%S")
            payload = {
                "event_id": event_id, "event": transition.event_type,
                "event_time": mk, "event_time_iso": now.isoformat(timespec="milliseconds"),
                "monotonic_ns": transition.confirmed_at_monotonic_ns,
                "wall_clock_synced": True, "device_id": DEVICE_ID,
                "score": transition.latest_score, "schema_version": 1,
            }
            print(f"\n      🟢 FSM {transition.from_state} → {transition.to_state} "
                  f"({transition.event_type}) score={transition.latest_score:.2f}")
            print(f"         publish event_id={event_id[:8]}.. mk={mk}")
            det_mqtt.publish_event("presence/event", payload, qos=2)
            if transition.event_type == "ENTER": enter_count += 1
            else: exit_count += 1

        sender.run_once(now=datetime.now(UTC))

        now_t = time.monotonic()
        if now_t - last_log >= 3.0:
            print(f"      [stats] frames={frames} detections={detections} "
                  f"state={fsm.state} infer_p50={r.infer_ms:.0f}ms "
                  f"enter={enter_count} exit={exit_count}")
            frames = detections = 0; last_log = now_t

        if enter_count > 0 and exit_count > 0 and len(acks) >= 2:
            print(f"\n[5/6] ENTER+EXIT cycle complete with both ACKs"); break

        elapsed = time.monotonic() - t0
        if elapsed < period: time.sleep(period - elapsed)

    cap.release()

    print(f"\n[5/6] verifying rows in live HF1RCM01")
    conn = open_connection(PROFILES[PROFILE]["oracle"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS FROM HF1RCM01 "
                "WHERE STA_NO1='TST' AND STA_NO2='T' AND STA_NO3='00' ORDER BY MK_DATE"
            )
            rows = cur.fetchall()
            for r in rows:
                marker = "ENTER" if r[4] == 1 else "EXIT"
                print(f"      ✓ MK={r[0]} STA={r[1]}/{r[2]}/{r[3]} status={r[4]} ({marker})")
            print(f"      total: {len(rows)} TST rows")
        if rows:
            print(f"\n[6/6] cleanup TST rows")
            with conn.cursor() as cur:
                cur.execute("DELETE FROM HF1RCM01 WHERE STA_NO1='TST' AND STA_NO2='T' AND STA_NO3='00'")
                print(f"      deleted {cur.rowcount}")
            conn.commit()
    finally:
        conn.close()

    bridge_mqtt.disconnect(); det_mqtt.disconnect()
    loop.call_soon_threadsafe(stop.set)
    print(f"\n=== DONE: enter={enter_count} exit={exit_count} acks={len(acks)} ===")
    return 0 if (enter_count > 0 and exit_count > 0) else 1


if __name__ == "__main__":
    sys.exit(main())
