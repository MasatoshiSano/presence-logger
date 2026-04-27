import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from services.bridge.src.circuit_breaker import CircuitBreaker
from services.bridge.src.inbox import InboxEvent, InboxRepository
from services.bridge.src.network_watcher import NetworkWatcher
from services.bridge.src.oracle_client import MergeResult
from services.bridge.src.profile_resolver import ProfileResolver

# next_retry_at logic mirrors the detector retry module to keep the bridge self-contained.
from services.bridge.src.retry import BackoffPolicy, next_retry_at
from services.bridge.src.time_correction import correct_event_wall, format_mk_date_jst
from services.bridge.src.time_watcher import TimeWatcher

_log = logging.getLogger("bridge.sender")


class _OracleProto:
    def execute_merge_for_profile(
        self,
        *,
        profile: dict,
        mk_date: str,
        sta_no1: str,
        sta_no2: str,
        sta_no3: str,
        t1_status: int,
    ) -> MergeResult: ...


class _MqttProto:
    def publish_ack(
        self,
        topic: str,
        *,
        event_id: str,
        mk_date_committed: str,
        committed_at_iso: str,
    ) -> None: ...


@dataclass
class SenderDeps:
    inbox: InboxRepository
    resolver: ProfileResolver
    breaker: CircuitBreaker
    network: NetworkWatcher
    time_watcher: TimeWatcher
    oracle: _OracleProto
    mqtt: _MqttProto
    device_cfg: dict[str, Any]
    topic_ack: str
    backoff_policy: BackoffPolicy = field(
        default_factory=lambda: BackoffPolicy(initial=5.0, multiplier=3.0, cap=600.0)
    )


class Sender:
    def __init__(self, *, deps: SenderDeps):
        self._d = deps

    def run_once(self, *, now: datetime) -> None:
        ssid = self._d.network.get_current_ssid()
        decision = self._d.resolver.resolve(ssid)
        if decision.action != "send":
            return
        profile_name = decision.profile_name
        if profile_name is None:
            return
        if self._d.breaker.state_for(profile_name, now=now) == "open":
            return

        profile = self._d.resolver.get(profile_name)
        for event in self._d.inbox.iter_received_due(now_iso=now.isoformat()):
            mk_date = self._resolve_mk_date(event)
            if mk_date is None:
                # SNTP not synced and event has no wall clock — defer.
                continue
            self._send_one(
                event=event,
                profile=profile,
                profile_name=profile_name,
                mk_date=mk_date,
                now=now,
            )

    def _resolve_mk_date(self, event: InboxEvent) -> str | None:
        if event.wall_synced and event.mk_date:
            return event.mk_date
        # Need to backfill from monotonic baseline.
        baseline = self._d.time_watcher.baseline
        if baseline is None:
            return None
        wall = correct_event_wall(
            sync_wall=baseline.sync_wall,
            sync_monotonic_ns=baseline.sync_monotonic_ns,
            event_monotonic_ns=event.monotonic_ns,
        )
        return format_mk_date_jst(wall)

    def _send_one(
        self,
        *,
        event: InboxEvent,
        profile: dict,
        profile_name: str,
        mk_date: str,
        now: datetime,
    ) -> None:
        sta = self._d.device_cfg["station"]
        t1_status = 1 if event.event_type == "ENTER" else 2
        result = self._d.oracle.execute_merge_for_profile(
            profile=profile,
            mk_date=mk_date,
            sta_no1=sta["sta_no1"],
            sta_no2=sta["sta_no2"],
            sta_no3=sta["sta_no3"],
            t1_status=t1_status,
        )
        if result.ora_code is None:
            self._d.inbox.mark_sent(
                event.event_id,
                mk_date_committed=mk_date,
                profile_at_send=profile_name,
                sent_at_iso=now.isoformat(),
            )
            self._d.breaker.record_success(profile_name, now=now)
            self._d.mqtt.publish_ack(
                self._d.topic_ack,
                event_id=event.event_id,
                mk_date_committed=mk_date,
                committed_at_iso=now.isoformat(timespec="milliseconds"),
            )
            _log.info(
                "merge_committed",
                extra={
                    "event": "merge_committed",
                    "event_id": event.event_id,
                    "mk_date": mk_date,
                    "rows_affected": result.rows_affected,
                    "profile": profile_name,
                },
            )
        else:
            self._d.breaker.record_failure(profile_name, ora_code=result.ora_code, now=now)
            attempt = event.retry_count + 1
            next_at = next_retry_at(
                now, attempt=attempt, policy=self._d.backoff_policy
            ).isoformat()
            self._d.inbox.update_retry(
                event.event_id,
                retry_count=attempt,
                next_retry_at_iso=next_at,
                last_error=f"ORA-{result.ora_code}: {result.error_message}",
            )
            _log.error(
                "merge_failed",
                extra={
                    "event": "merge_failed",
                    "event_id": event.event_id,
                    "ora_code": result.ora_code,
                    "retry_count": attempt,
                },
            )
