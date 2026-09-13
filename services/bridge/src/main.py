from __future__ import annotations

import json
import logging
import os
import signal
import time
from datetime import UTC, datetime
from pathlib import Path

from services.bridge.src import config as cfg_mod
from services.bridge.src.circuit_breaker import CircuitBreaker
from services.bridge.src.inbox import InboxEvent, InboxRepository
from services.bridge.src.liveness import LivenessTracker, device_id_from_topic
from services.bridge.src.logging_setup import setup_logging
from services.bridge.src.mqtt_file_log import MqttFileLog, resolve_mqtt_log_path
from services.bridge.src.mqtt_listener import BridgeMqttClient, EventPayload
from services.bridge.src.network_watcher import NetworkWatcher
from services.bridge.src.oracle_client import (
    MergeResult,
    init_oracle_client_for_profiles,
    open_and_merge,
)
from services.bridge.src.oracle_jdbc_client import execute_merge_via_jdbc
from services.bridge.src.profile_resolver import ProfileResolver
from services.bridge.src.record import parse_record_payload
from services.bridge.src.record_inbox import RecordInboxEvent, RecordInboxRepository
from services.bridge.src.record_sender import RecordSender, RecordSenderDeps
from services.bridge.src.retry import BackoffPolicy
from services.bridge.src.sender import Sender, SenderDeps
from services.bridge.src.time_watcher import TimeWatcher

_log = logging.getLogger("bridge.main")
HEALTH_FILE = "/tmp/bridge.healthy"        # noqa: S108
DEFAULT_BRIDGE_YAML = "/etc/presence-logger/bridge.yaml"
DEFAULT_DEVICE_YAML = "/etc/presence-logger/device.yaml"
DEFAULT_PROFILES_YAML = "/etc/presence-logger/profiles.yaml"
DEFAULT_UPCMPFLG_OVERRIDE = cfg_mod.UPCMPFLG_OVERRIDE_FILE


class _OracleAdapter:
    """Dispatches each MERGE to either python-oracledb (thin/thick) or the
    oracle-jdbc sidecar, based on the profile's client_mode."""

    def __init__(self, *, jdbc_cfg: dict, upcmpflg_override_path: Path | None = None):
        self._jdbc_proxy_url = jdbc_cfg["url"]
        self._jdbc_connect_timeout_ms = int(jdbc_cfg["connect_timeout_ms"])
        self._jdbc_read_timeout_ms = int(jdbc_cfg["read_timeout_ms"])
        self._upcmpflg_override_path = upcmpflg_override_path

    def execute_merge_for_profile(
        self,
        *,
        profile: dict,
        mk_date: str,
        sta_no1: str,
        sta_no2: str,
        sta_no3: str,
        t1_status: int,
    ) -> MergeResult:
        oracle_cfg = profile["oracle"]
        # Live override wins over profiles.yaml's static value so an operator
        # can flip UPCMPFLG without a bridge restart (see read_upcmpflg_override).
        # No override path configured -> never touch the filesystem, same as
        # before this feature existed.
        upcmpflg = None
        if self._upcmpflg_override_path is not None:
            upcmpflg = cfg_mod.read_upcmpflg_override(self._upcmpflg_override_path)
        if upcmpflg is None:
            upcmpflg = oracle_cfg.get("upcmpflg")
        if upcmpflg is not None:
            upcmpflg = int(upcmpflg)
        if oracle_cfg.get("client_mode") == "jdbc":
            return execute_merge_via_jdbc(
                oracle_cfg,
                proxy_url=self._jdbc_proxy_url,
                table_name=oracle_cfg["table_name"],
                mk_date=mk_date,
                sta_no1=sta_no1,
                sta_no2=sta_no2,
                sta_no3=sta_no3,
                t1_status=t1_status,
                upcmpflg=upcmpflg,
                connect_timeout_ms=self._jdbc_connect_timeout_ms,
                read_timeout_ms=self._jdbc_read_timeout_ms,
            )
        return open_and_merge(
            oracle_cfg,
            table_name=oracle_cfg["table_name"],
            mk_date=mk_date,
            sta_no1=sta_no1,
            sta_no2=sta_no2,
            sta_no3=sta_no3,
            t1_status=t1_status,
            upcmpflg=upcmpflg,
        )


def main() -> int:    # pragma: no cover
    bridge_cfg = cfg_mod.load_bridge_config(
        Path(os.environ.get("BRIDGE_YAML", DEFAULT_BRIDGE_YAML))
    )
    device_cfg = cfg_mod.load_device_config(
        Path(os.environ.get("DEVICE_YAML", DEFAULT_DEVICE_YAML))
    )
    profiles_cfg = cfg_mod.load_profiles_config(
        Path(os.environ.get("PROFILES_YAML", DEFAULT_PROFILES_YAML))
    )

    setup_logging(
        process="bridge",
        device_id=device_cfg["device_id"],
        log_dir="/var/log/presence-logger",
        level=bridge_cfg["logging"]["level"],
    )
    _log.info("startup", extra={"event": "startup"})

    init_oracle_client_for_profiles(
        profiles_cfg["profiles"],
        instant_client_dir=bridge_cfg["oracle"]["instant_client_dir"],
    )

    inbox = InboxRepository(bridge_cfg["buffer"]["path"])
    inbox.init()
    record_cfg = bridge_cfg.get("record", {})
    record_inbox = RecordInboxRepository(
        record_cfg.get("buffer_path", "/var/lib/presence-logger/bridge_record_buf.db")
    )
    record_inbox.init()
    resolver = ProfileResolver(
        profiles=profiles_cfg["profiles"],
        unknown_policy=profiles_cfg["unknown_ssid_policy"],
    )
    breaker = CircuitBreaker(
        half_open_after_seconds=bridge_cfg["circuit_breaker"]["half_open_after_seconds"],
        permanent_codes=set(bridge_cfg["circuit_breaker"]["permanent_ora_codes"]),
    )
    network = NetworkWatcher(
        command=bridge_cfg["network_watcher"]["ssid_command"],
        # In a dual-WiFi setup several SSIDs are active at once; report the one
        # we have a profile for (factory net) so events are not dropped.
        preferred_ssids=set(profiles_cfg["profiles"]),
    )
    time_watcher = TimeWatcher(command=bridge_cfg["time_watcher"]["sync_command"])
    oracle_adapter = _OracleAdapter(
        jdbc_cfg=bridge_cfg["oracle_jdbc"],
        upcmpflg_override_path=Path(
            os.environ.get("UPCMPFLG_OVERRIDE", DEFAULT_UPCMPFLG_OVERRIDE)
        ),
    )

    mqtt = BridgeMqttClient(client_id=bridge_cfg["mqtt"]["client_id"])
    mqtt.connect_and_loop(
        host=os.environ.get("MQTT_HOST", bridge_cfg["mqtt"]["host"]),
        port=bridge_cfg["mqtt"]["port"],
    )

    def _on_event(payload: EventPayload, raw: bytes) -> None:
        # Enforce unknown_ssid_policy at receive time, not just at send time.
        # When policy=drop and we're on an unknown SSID (e.g. dev/home Wi-Fi),
        # events are discarded immediately instead of being kept in the inbox
        # to be flushed later. This matches the "don't write events captured
        # under a non-production SSID" requirement.
        decision = resolver.peek(network.cached_ssid)
        if decision.action == "drop":
            _log.info(
                "drop_unknown_ssid",
                extra={
                    "event": "drop_unknown_ssid",
                    "event_id": payload.event_id,
                    "ssid": network.cached_ssid,
                },
            )
            return
        event = InboxEvent(
            event_id=payload.event_id,
            event_type=payload.event_type,
            mk_date=payload.mk_date,
            monotonic_ns=payload.monotonic_ns,
            wall_synced=payload.wall_clock_synced,
            device_id=payload.device_id,
            score=payload.score,
            raw_payload=raw.decode("utf-8", errors="replace"),
            status="received",
            ssid_at_receive=network.cached_ssid,
            profile_at_send=None,
            mk_date_committed=None,
            received_at_iso=datetime.now(UTC).isoformat(),
            sent_at_iso=None,
            retry_count=0,
            next_retry_at_iso=None,
            last_error=None,
        )
        inbox.insert_received(event)
        _log.info("received", extra={"event": "received", "event_id": payload.event_id})

    mqtt.subscribe_event(bridge_cfg["mqtt"]["topic_event"], _on_event)

    # --- child-device liveness (status + heartbeat) ---
    liv_cfg = bridge_cfg.get("liveness", {})
    status_prefix = liv_cfg.get("status_topic_prefix", "presence/status/")
    hb_prefix = liv_cfg.get("heartbeat_topic_prefix", "presence/heartbeat/")
    hb_timeout = liv_cfg.get("heartbeat_timeout_seconds", 60)
    liveness_report_interval = liv_cfg.get("report_interval_seconds", 30)
    liveness = LivenessTracker(heartbeat_timeout_seconds=hb_timeout)
    mqtt_file = MqttFileLog(resolve_mqtt_log_path(liv_cfg))
    if mqtt_file.enabled:
        _log.info(
            "mqtt_file_log_enabled",
            extra={"event": "mqtt_file_log_enabled", "path": str(mqtt_file.path)},
        )
    elif mqtt_file.path:
        _log.warning(
            "mqtt_file_log_disabled",
            extra={
                "event": "mqtt_file_log_disabled",
                "path": str(mqtt_file.path),
                "error": {"message": mqtt_file.error or "unknown"},
            },
        )

    def _on_status(topic: str, payload: bytes) -> None:
        text = payload.decode("utf-8", errors="replace").strip()
        dev = device_id_from_topic(topic, status_prefix)
        if dev:
            liveness.record_status(dev, text, now=time.monotonic())
            mqtt_file.write("status", dev, text)

    def _on_heartbeat(topic: str, payload: bytes) -> None:
        dev = device_id_from_topic(topic, hb_prefix)
        if not dev:
            return
        text = payload.decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                data = {}
        except json.JSONDecodeError:
            data = {}
        liveness.record_heartbeat(dev, data, now=time.monotonic())
        mqtt_file.write("heartbeat", dev, text)

    mqtt.subscribe_text(status_prefix + "#", _on_status)
    mqtt.subscribe_text(hb_prefix + "#", _on_heartbeat)

    # --- child-Pi records (presence/record): fully-formed Oracle rows ---
    record_topic = record_cfg.get("topic", "presence/record")
    record_ack_topic = record_cfg.get("topic_ack", "presence/record/ack")

    def _on_record(topic: str, payload: bytes) -> None:
        raw_text = payload.decode("utf-8", errors="replace")
        try:
            rec = parse_record_payload(payload)
        except ValueError as e:
            mqtt_file.write("record", "", raw_text)
            _log.warning(
                "record_parse_failed",
                extra={
                    "event": "record_parse_failed",
                    "error": {"type": type(e).__name__, "message": str(e)},
                },
            )
            return
        record_inbox.insert_received(RecordInboxEvent(
            event_id=rec.event_id,
            mk_date=rec.mk_date,
            sta_no1=rec.sta_no1,
            sta_no2=rec.sta_no2,
            sta_no3=rec.sta_no3,
            t1_status=rec.t1_status,
            device_id=rec.device_id,
            raw_payload=raw_text,
            status="received",
            received_at_iso=datetime.now(UTC).isoformat(),
            sent_at_iso=None,
            mk_date_committed=None,
            retry_count=0,
            next_retry_at_iso=None,
            last_error=None,
        ))
        _log.info(
            "record_received",
            extra={"event": "record_received", "event_id": rec.event_id,
                   "device_id": rec.device_id},
        )
        mqtt_file.write("record", rec.device_id or "", raw_text)

    mqtt.subscribe_text(record_topic, _on_record)

    sender = Sender(deps=SenderDeps(
        inbox=inbox,
        resolver=resolver,
        breaker=breaker,
        network=network,
        time_watcher=time_watcher,
        oracle=oracle_adapter,
        mqtt=mqtt,
        device_cfg=device_cfg,
        topic_ack=bridge_cfg["mqtt"]["topic_ack"],
        backoff_policy=BackoffPolicy(
            initial=bridge_cfg["retry"]["initial_delay_seconds"],
            multiplier=bridge_cfg["retry"]["multiplier"],
            cap=bridge_cfg["retry"]["max_delay_seconds"],
        ),
    ))

    record_sender = RecordSender(deps=RecordSenderDeps(
        record_inbox=record_inbox,
        resolver=resolver,
        breaker=breaker,
        network=network,
        oracle=oracle_adapter,
        mqtt=mqtt,
        topic_ack=record_ack_topic,
        backoff_policy=BackoffPolicy(
            initial=bridge_cfg["retry"]["initial_delay_seconds"],
            multiplier=bridge_cfg["retry"]["multiplier"],
            cap=bridge_cfg["retry"]["max_delay_seconds"],
        ),
        unretryable_ora_codes=frozenset(
            bridge_cfg["circuit_breaker"].get("unretryable_ora_codes", [])
        ),
    ))

    running = True

    def _stop(*_a):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    last_health = 0.0
    last_stats = 0.0
    last_network = 0.0
    last_time = 0.0
    last_liveness = 0.0

    while running:
        now = time.monotonic()

        if now - last_network >= bridge_cfg["network_watcher"]["poll_interval_seconds"]:
            network.get_current_ssid()
            last_network = now
        if now - last_time >= bridge_cfg["time_watcher"]["poll_interval_seconds"]:
            time_watcher.poll()
            last_time = now

        sender.run_once(now=datetime.now(UTC))
        record_sender.run_once(now=datetime.now(UTC))

        if now - last_health >= 5.0:
            Path(HEALTH_FILE).touch()
            last_health = now
        if now - last_stats >= bridge_cfg["logging"]["buffer_stats_interval_seconds"]:
            _log.info(
                "periodic",
                extra={
                    "event": "periodic",
                    "current_ssid": network.cached_ssid,
                    "ntp_synced": time_watcher.is_synced,
                    "inbox_count": inbox.count(),
                    "record_inbox_count": record_inbox.count(),
                },
            )
            last_stats = now
        if now - last_liveness >= liveness_report_interval:
            devices = liveness.snapshot(now=now)
            down = [d for d in devices if d["state"] in ("offline", "stale")]
            _log.info(
                "liveness",
                extra={
                    "event": "liveness",
                    "devices_total": len(devices),
                    "devices_down": len(down),
                    "devices": devices,
                },
            )
            for d in down:
                _log.warning(
                    "device_down",
                    extra={
                        "event": "device_down",
                        "device_id": d["device_id"],
                        "state": d["state"],
                        "age_seconds": d["age_seconds"],
                    },
                )
            last_liveness = now

        time.sleep(1.0)

    mqtt.disconnect()
    return 0


if __name__ == "__main__":     # pragma: no cover
    raise SystemExit(main())
