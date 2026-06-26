"""Sends buffered child-Pi records to Oracle.

Mirrors Sender but for the record path: each record already carries MK_DATE /
STA_NO1-3 / T1_STATUS, so there is no ENTER/EXIT mapping and no profile station
lookup — the row's own values go straight into the same MERGE. Only writes while
on a known profile SSID (peek, non-mutating) with the breaker closed.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from services.bridge.src.circuit_breaker import CircuitBreaker
from services.bridge.src.network_watcher import NetworkWatcher
from services.bridge.src.profile_resolver import ProfileResolver
from services.bridge.src.record_inbox import RecordInboxEvent, RecordInboxRepository
from services.bridge.src.retry import BackoffPolicy, next_retry_at

_log = logging.getLogger("bridge.record_sender")


@dataclass
class RecordSenderDeps:
    record_inbox: RecordInboxRepository
    resolver: ProfileResolver
    breaker: CircuitBreaker
    network: NetworkWatcher
    oracle: Any           # execute_merge_for_profile(...)
    mqtt: Any             # publish_ack(...)
    topic_ack: str | None
    backoff_policy: BackoffPolicy = field(
        default_factory=lambda: BackoffPolicy(initial=5.0, multiplier=3.0, cap=600.0)
    )


class RecordSender:
    def __init__(self, *, deps: RecordSenderDeps):
        self._d = deps

    def run_once(self, *, now: datetime) -> None:
        # peek (non-mutating) — the event Sender owns the resolver's _last_known.
        decision = self._d.resolver.peek(self._d.network.cached_ssid)
        if decision.action != "send" or decision.profile_name is None:
            return
        profile_name = decision.profile_name
        if self._d.breaker.state_for(profile_name, now=now) == "open":
            return
        profile = self._d.resolver.get(profile_name)
        for rec in self._d.record_inbox.iter_received_due(now_iso=now.isoformat()):
            self._send_one(rec=rec, profile=profile, profile_name=profile_name, now=now)

    def _send_one(self, *, rec: RecordInboxEvent, profile: dict, profile_name: str,
                  now: datetime) -> None:
        result = self._d.oracle.execute_merge_for_profile(
            profile=profile,
            mk_date=rec.mk_date,
            sta_no1=rec.sta_no1,
            sta_no2=rec.sta_no2,
            sta_no3=rec.sta_no3,
            t1_status=rec.t1_status,
        )
        if result.ora_code is None:
            self._d.record_inbox.mark_sent(
                rec.event_id, mk_date_committed=rec.mk_date, sent_at_iso=now.isoformat()
            )
            self._d.breaker.record_success(profile_name, now=now)
            if self._d.topic_ack:
                self._d.mqtt.publish_ack(
                    self._d.topic_ack,
                    event_id=rec.event_id,
                    mk_date_committed=rec.mk_date,
                    committed_at_iso=now.isoformat(timespec="milliseconds"),
                )
            _log.info(
                "record_committed",
                extra={
                    "event": "record_committed",
                    "event_id": rec.event_id,
                    "mk_date": rec.mk_date,
                    "t1_status": rec.t1_status,
                    "rows_affected": result.rows_affected,
                    "profile": profile_name,
                },
            )
        else:
            self._d.breaker.record_failure(profile_name, ora_code=result.ora_code, now=now)
            attempt = rec.retry_count + 1
            next_at = next_retry_at(now, attempt=attempt, policy=self._d.backoff_policy).isoformat()
            self._d.record_inbox.update_retry(
                rec.event_id,
                retry_count=attempt,
                next_retry_at_iso=next_at,
                last_error=f"ORA-{result.ora_code}: {result.error_message}",
            )
            _log.error(
                "record_merge_failed",
                extra={
                    "event": "record_merge_failed",
                    "event_id": rec.event_id,
                    "ora_code": result.ora_code,
                    "retry_count": attempt,
                },
            )
