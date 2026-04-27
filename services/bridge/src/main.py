from __future__ import annotations

import logging
import os
import signal
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.bridge.src import config as cfg_mod
from services.bridge.src.circuit_breaker import CircuitBreaker
from services.bridge.src.inbox import InboxEvent, InboxRepository
from services.bridge.src.logging_setup import setup_logging
from services.bridge.src.mqtt_listener import BridgeMqttClient, EventPayload
from services.bridge.src.network_watcher import NetworkWatcher
from services.bridge.src.oracle_client import (
    MergeResult,
    execute_merge,
    init_oracle_client_for_profiles,
    open_connection,
)
from services.bridge.src.profile_resolver import ProfileResolver
from services.bridge.src.retry import BackoffPolicy
from services.bridge.src.sender import Sender, SenderDeps
from services.bridge.src.time_watcher import TimeWatcher

_log = logging.getLogger("bridge.main")
HEALTH_FILE = "/tmp/bridge.healthy"        # noqa: S108
DEFAULT_BRIDGE_YAML = "/etc/presence-logger/bridge.yaml"
DEFAULT_DEVICE_YAML = "/etc/presence-logger/device.yaml"
DEFAULT_PROFILES_YAML = "/etc/presence-logger/profiles.yaml"


class _OracleAdapter:
    """Adapts open_connection + execute_merge into the SenderDeps oracle protocol."""

    def __init__(self):
        self._cache: dict[str, Any] = {}

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
        # Connection per call is acceptable for INSERT-only workloads at 1-2 events/min.
        conn = open_connection(oracle_cfg)
        try:
            return execute_merge(
                conn,
                table_name=oracle_cfg["table_name"],
                mk_date=mk_date,
                sta_no1=sta_no1,
                sta_no2=sta_no2,
                sta_no3=sta_no3,
                t1_status=t1_status,
            )
        finally:
            try:
                conn.close()
            except Exception:    # noqa: BLE001, S110
                pass


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
    resolver = ProfileResolver(
        profiles=profiles_cfg["profiles"],
        unknown_policy=profiles_cfg["unknown_ssid_policy"],
    )
    breaker = CircuitBreaker(
        half_open_after_seconds=bridge_cfg["circuit_breaker"]["half_open_after_seconds"],
        permanent_codes=set(bridge_cfg["circuit_breaker"]["permanent_ora_codes"]),
    )
    network = NetworkWatcher(command=bridge_cfg["network_watcher"]["ssid_command"])
    time_watcher = TimeWatcher(command=bridge_cfg["time_watcher"]["sync_command"])
    oracle_adapter = _OracleAdapter()

    mqtt = BridgeMqttClient(client_id=bridge_cfg["mqtt"]["client_id"])
    mqtt.connect_and_loop(
        host=os.environ.get("MQTT_HOST", bridge_cfg["mqtt"]["host"]),
        port=bridge_cfg["mqtt"]["port"],
    )

    def _on_event(payload: EventPayload, raw: bytes) -> None:
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

    while running:
        now = time.monotonic()

        if now - last_network >= bridge_cfg["network_watcher"]["poll_interval_seconds"]:
            network.get_current_ssid()
            last_network = now
        if now - last_time >= bridge_cfg["time_watcher"]["poll_interval_seconds"]:
            time_watcher.poll()
            last_time = now

        sender.run_once(now=datetime.now(UTC))

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
                },
            )
            last_stats = now

        time.sleep(1.0)

    mqtt.disconnect()
    return 0


if __name__ == "__main__":     # pragma: no cover
    raise SystemExit(main())
