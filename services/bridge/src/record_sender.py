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
    mqtt: Any             # publish_ack(...), publish_nack(...)
    topic_ack: str | None
    topic_nack: str | None = None
    backoff_policy: BackoffPolicy = field(
        default_factory=lambda: BackoffPolicy(initial=5.0, multiplier=3.0, cap=600.0)
    )
    # ORA番号(整数)。この行を送っても構造的に絶対成功しない(例: ORA-00001 主キー
    # 重複=別の行が既に同じMK_DATE+STA_NOで成功済み)場合に、この行"だけ"を
    # 諦める。breaker.permanent_codes とは別物: あちらはプロファイル全体の接続
    # 不良を表しサーキットを開いて他の行も止めるが、主キー重複は他の正常な行
    # まで止める理由にならないため、行単位でstatus='failed'にして無限リトライ
    # を止めるだけに留める。
    unretryable_ora_codes: frozenset[int] = frozenset()


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

    def receive(self, rec: RecordInboxEvent) -> None:
        """Handle a re-received record (child resending because it has no ACK yet).

        A first-time event_id is buffered normally (received -> picked up by
        run_once). A duplicate of an already-committed row only gets a fresh
        ACK when its content still matches what was committed — content drift
        under the same event_id is rejected, never re-acked. A duplicate of a
        'received' (still pending) row is a no-op: the existing row already
        owns that event_id's outcome. A duplicate of a 'failed' (given up)
        row re-publishes the nack — the child resending it is exactly the
        signal that it never received (or was offline for) the original one.
        """
        existing = self._d.record_inbox.get(rec.event_id)
        if existing is None:
            self._d.record_inbox.insert_received(rec)
            return
        if existing.status == "failed":
            if self._d.topic_nack and self._content_matches(existing, rec):
                self._d.mqtt.publish_nack(
                    self._d.topic_nack,
                    event_id=existing.event_id,
                    reason=existing.last_error or "unretryable",
                    failed_at_iso=existing.failed_at_iso or "",
                )
            return
        if existing.status != "sent":
            return
        if not self._content_matches(existing, rec):
            _log.warning(
                "record_duplicate_content_mismatch",
                extra={
                    "event": "record_duplicate_content_mismatch",
                    "event_id": rec.event_id,
                },
            )
            return
        if self._d.topic_ack:
            self._d.mqtt.publish_ack(
                self._d.topic_ack,
                event_id=existing.event_id,
                mk_date_committed=existing.mk_date_committed,
                committed_at_iso=existing.sent_at_iso,
            )

    @staticmethod
    def _content_matches(stored: RecordInboxEvent, incoming: RecordInboxEvent) -> bool:
        return (
            stored.device_id == incoming.device_id
            and stored.mk_date == incoming.mk_date
            and stored.sta_no1 == incoming.sta_no1
            and stored.sta_no2 == incoming.sta_no2
            and stored.sta_no3 == incoming.sta_no3
            and stored.t1_status == incoming.t1_status
        )

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
        if result.ora_code is None and result.error_message is None:
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
        elif result.ora_code in self._d.unretryable_ora_codes:
            failed_at_iso = now.isoformat()
            last_error = f"ORA-{result.ora_code}: {result.error_message} (諦め)"
            self._d.record_inbox.mark_failed(
                rec.event_id, failed_at_iso=failed_at_iso, last_error=last_error,
            )
            if self._d.topic_nack:
                self._d.mqtt.publish_nack(
                    self._d.topic_nack,
                    event_id=rec.event_id,
                    reason=last_error,
                    failed_at_iso=failed_at_iso,
                )
            _log.warning(
                "record_giveup",
                extra={
                    "event": "record_giveup",
                    "event_id": rec.event_id,
                    "ora_code": result.ora_code,
                    "mk_date": rec.mk_date,
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
