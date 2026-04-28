#!/usr/bin/env python3
"""Full live end-to-end pipeline test.

Pieces wired up in-process (no Docker, no system mosquitto):
- amqtt broker on 127.0.0.1:1883 (Python MQTT broker)
- BridgeMqttClient (paho) subscribing to presence/event with QoS=2
- Real InboxRepository (SQLite, /tmp/presence-live/inbox.db)
- Real Oracle MERGE against the live Cloud ADB (eqstatusdb_low) using the wallet
- Real ACK publication on presence/event/ack
- A simulated detector that publishes a single ENTER then a single EXIT
  (replaces the camera + MediaPipe + FSM layers, which are unit-tested separately)

Verifies that an MQTT-published ENTER/EXIT actually lands in HF1RCM01, then cleans
up the test rows so production data is untouched.
"""
from __future__ import annotations

import asyncio
import os
import json
import shutil
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import paho.mqtt.client as paho
from amqtt.broker import Broker

from services.bridge.src.circuit_breaker import CircuitBreaker
from services.bridge.src.inbox import InboxEvent, InboxRepository
from services.bridge.src.mqtt_listener import BridgeMqttClient, EventPayload
from services.bridge.src.oracle_client import execute_merge, open_connection
from services.bridge.src.profile_resolver import ProfileResolver
from services.bridge.src.retry import BackoffPolicy
from services.bridge.src.sender import Sender, SenderDeps

# --- Test fixtures ----------------------------------------------------------------

WORK = Path("/tmp/presence-live")
shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(parents=True, exist_ok=True)

PROFILE_NAME = "live_test_wifi"
DEVICE_ID = "rpi-live-test"
PROFILES = {
    PROFILE_NAME: {
        "description": "live ADB",
        "sntp": {"servers": ["ntp.nict.jp"]},
        "oracle": {
            "client_mode": "thin",
            "auth_mode": "wallet",
            "dsn": "eqstatusdb_low",
            "user": "ADMIN",
            "password": os.environ.get("ORACLE_PASSWORD_ADB", ""),
            "wallet_dir": "/home/pi/oracle_wallet",
            "wallet_password": os.environ.get("WALLET_PASSWORD_ADB", ""),
            "table_name": "HF1RCM01",
        },
    }
}
DEVICE_CFG = {
    "device_id": DEVICE_ID,
    "station": {"sta_no1": "TST", "sta_no2": "T", "sta_no3": "00"},
}


# --- amqtt broker control ---------------------------------------------------------

BROKER_CONFIG = {
    "listeners": {"default": {"type": "tcp", "bind": "127.0.0.1:1883"}},
    "sys_interval": 0,
    "auth": {"allow-anonymous": True},
    "topic-check": {"enabled": False},
}


async def _run_broker(stop_event: asyncio.Event) -> None:
    broker = Broker(BROKER_CONFIG)
    await broker.start()
    try:
        await stop_event.wait()
    finally:
        await broker.shutdown()


def start_broker_thread() -> tuple[threading.Thread, asyncio.Event, asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    stop_event = asyncio.Event()

    def _main():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_broker(stop_event))
        loop.close()

    t = threading.Thread(target=_main, daemon=True)
    t.start()
    # Wait until broker is listening.
    deadline = time.monotonic() + 10.0
    import socket as _s
    while time.monotonic() < deadline:
        try:
            with _s.create_connection(("127.0.0.1", 1883), timeout=0.5):
                return t, stop_event, loop
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("amqtt broker failed to start within 10s")


# --- Oracle adapter (mirrors bridge.main._OracleAdapter) --------------------------

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


# --- Network/time fakes (we're not really resolving SSID or syncing NTP here) -----

class FakeNetwork:
    cached_ssid = PROFILE_NAME
    def get_current_ssid(self):  # noqa: D401
        return PROFILE_NAME


class FakeTimeWatcher:
    is_synced = True
    baseline = None  # not needed; events come pre-synced


# --- Test orchestration -----------------------------------------------------------

def publish_event(client: paho.Client, *, event_id: str, event_type: str, mk_date: str) -> None:
    payload = {
        "event_id": event_id,
        "event": event_type,
        "event_time": mk_date,
        "event_time_iso": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "monotonic_ns": time.monotonic_ns(),
        "wall_clock_synced": True,
        "device_id": DEVICE_ID,
        "score": 0.95,
        "schema_version": 1,
    }
    info = client.publish("presence/event", json.dumps(payload), qos=2)
    info.wait_for_publish(timeout=5.0)
    print(f"  -> published {event_type} event_id={event_id} mk_date={mk_date} (rc={info.rc})")


def main() -> int:
    print("=== Step 1: start amqtt broker on 127.0.0.1:1883 ===")
    t_broker, stop_event, loop = start_broker_thread()
    print("  broker up\n")

    inbox = InboxRepository(WORK / "inbox.db"); inbox.init()
    resolver = ProfileResolver(profiles=PROFILES, unknown_policy="hold")
    breaker = CircuitBreaker(half_open_after_seconds=900, permanent_codes={942, 904, 1017, 1031, 12514})
    oracle = OracleAdapter()

    # Bridge MQTT listener -- this is what main.py wires up.
    bridge_mqtt = BridgeMqttClient(client_id="presence-bridge-livetest")
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
        print(f"  bridge received: event_id={payload.event_id} type={payload.event_type}")

    bridge_mqtt.subscribe_event("presence/event", on_event)

    sender = Sender(deps=SenderDeps(
        inbox=inbox, resolver=resolver, breaker=breaker,
        network=FakeNetwork(), time_watcher=FakeTimeWatcher(),
        oracle=oracle, mqtt=bridge_mqtt,
        device_cfg=DEVICE_CFG, topic_ack="presence/event/ack",
        backoff_policy=BackoffPolicy(initial=5.0, multiplier=3.0, cap=600.0),
    ))

    # Detector-side ACK subscriber (just to prove the round-trip).
    det_client = paho.Client(client_id="presence-detector-livetest", protocol=paho.MQTTv311)
    det_client.connect("127.0.0.1", 1883)
    det_client.loop_start()

    def _on_ack(_c, _u, msg):
        body = json.loads(msg.payload.decode())
        acks.append(body["event_id"])
        print(f"  detector got ACK: event_id={body['event_id']} mk_date_committed={body['mk_date_committed']}")

    det_client.subscribe("presence/event/ack", qos=2)
    det_client.message_callback_add("presence/event/ack", _on_ack)
    time.sleep(0.5)  # give subscriptions time to settle

    # --- Simulate detector firing ENTER then EXIT ---
    print("\n=== Step 2: simulate detector ENTER (after 3-sec debounce confirmed) ===")
    enter_id = str(uuid.uuid4())
    enter_mk = "20991231235959"
    publish_event(det_client, event_id=enter_id, event_type="ENTER", mk_date=enter_mk)

    print("\n=== Step 3: drain bridge sender (poll up to 15s) ===")
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        sender.run_once(now=datetime.now(UTC))
        row = inbox.get(enter_id)
        if row and row.status == "sent" and enter_id in acks:
            print(f"  ENTER landed: inbox status={row.status}, ack received")
            break
        time.sleep(0.3)
    else:
        print("  ENTER never landed within 15s")
        return 2

    print("\n=== Step 4: simulate detector EXIT 5 sec later ===")
    time.sleep(1.0)
    exit_id = str(uuid.uuid4())
    exit_mk = "20991231235960"
    # Oracle MK_DATE is 14-char string; "23:59:60" is invalid wall time but valid as a string key.
    # Use a different second to avoid collision with ENTER's MERGE key.
    exit_mk = "21000101000000"
    publish_event(det_client, event_id=exit_id, event_type="EXIT", mk_date=exit_mk)

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        sender.run_once(now=datetime.now(UTC))
        row = inbox.get(exit_id)
        if row and row.status == "sent" and exit_id in acks:
            print(f"  EXIT landed: inbox status={row.status}, ack received")
            break
        time.sleep(0.3)
    else:
        print("  EXIT never landed within 15s")
        return 3

    print("\n=== Step 5: verify rows in live HF1RCM01 ===")
    conn = open_connection(PROFILES[PROFILE_NAME]["oracle"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS FROM HF1RCM01 "
                "WHERE STA_NO1='TST' AND STA_NO2='T' AND STA_NO3='00' ORDER BY MK_DATE"
            )
            rows = cur.fetchall()
            for r in rows:
                print(f"  HF1RCM01 row: {r}")
            assert any(r[0] == enter_mk and r[4] == 1 for r in rows), "ENTER row missing"
            assert any(r[0] == exit_mk and r[4] == 2 for r in rows), "EXIT row missing"
            print("  both ENTER and EXIT rows found ✓")

        print("\n=== Step 6: cleanup TST rows ===")
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM HF1RCM01 WHERE STA_NO1='TST' AND STA_NO2='T' AND STA_NO3='00'"
            )
            print(f"  deleted {cur.rowcount} TST/T/00 row(s)")
        conn.commit()
    finally:
        conn.close()

    bridge_mqtt.disconnect()
    det_client.loop_stop()
    det_client.disconnect()
    loop.call_soon_threadsafe(stop_event.set)
    t_broker.join(timeout=5)

    print("\n=== DONE: full pipeline live-validated ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
