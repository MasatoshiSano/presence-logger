"""子側の Oracle ACK/NACK 完了契約。設計: docs/2026-09-23-child-oracle-ack-design.md,
docs/2026-09-25-child-nack-design.md

MQTT publish の成功(QoS2 PUBCOMP)は Oracle への到達を意味しない。ブリッジが
Oracle commit後に presence/record/ack へ流す ACK を受け取って初めて配送完了
とする。ブリッジが恒久失敗と判定した行は presence/record/nack へ流し、二度と
再送してはならない。DeliveryStore は宛先(host:port/topic)＋event_idで状態を
永続管理し、再起動後もCSVとつき合わせて再開できるようにする。
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

RESEND_AFTER_SECONDS = 600.0
ACK_WAIT_SECONDS = 5.0
SUBACK_FAILURE = 0x80


def _schema_sql(table_name: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {table_name} (
  destination      TEXT NOT NULL,
  event_id         TEXT NOT NULL,
  mk_date          TEXT NOT NULL,
  payload          TEXT NOT NULL,
  status           TEXT NOT NULL
                   CHECK(status IN ('pending','acked','legacy_unverified','nacked')),
  last_attempt_at  REAL,
  acked_at         TEXT,
  nack_reason      TEXT,
  nacked_at        TEXT,
  PRIMARY KEY (destination, event_id)
);
"""


SCHEMA = _schema_sql("delivery")
PRAGMAS = ["PRAGMA journal_mode = WAL", "PRAGMA synchronous = NORMAL"]


class SendOutcome(str, Enum):
    ACKED = "acked"
    NACKED = "nacked"
    PENDING = "pending"


@dataclass
class DeliveryRow:
    event_id: str
    mk_date: str
    payload: dict
    status: str
    last_attempt_at: float | None
    acked_at: str | None
    nack_reason: str | None = None
    nacked_at: str | None = None


class DeliveryStore:
    """SQLite台帳。宛先(host:port/topic)ごとに event_id の配送状態を持つ。

    宛先が変わったら別の台帳として扱う(ACKを流用しない) — 同じdbファイルでも
    destinationが違えば見えない。
    """

    def __init__(self, path: Path | str, destination: str):
        self.path = str(path)
        self.destination = destination
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            for p in PRAGMAS:
                c.execute(p)
            self._ensure_schema(c)

    def _ensure_schema(self, c: sqlite3.Connection) -> None:
        row = c.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='delivery'"
        ).fetchone()
        if row is None:
            c.executescript(SCHEMA)
            return
        if "'nacked'" in row["sql"]:
            return
        self._migrate(c)

    def _migrate(self, c: sqlite3.Connection) -> None:
        # SQLiteはCHECK制約をALTERできないため、新スキーマの表へ作り直して移す。
        # 失敗時はROLLBACKして例外を上げる(起動失敗=systemdが再起動。黙って旧
        # スキーマで動かさない)。
        try:
            c.execute("BEGIN IMMEDIATE")
            # executescript()はpending transactionを暗黙COMMITしてしまうため使わない
            # (BEGIN IMMEDIATEが消えてしまう)。schemaは単一文なのでexecute()で足りる。
            c.execute(_schema_sql("delivery_new"))
            c.execute(
                """
                INSERT INTO delivery_new
                  (destination, event_id, mk_date, payload, status,
                   last_attempt_at, acked_at)
                SELECT destination, event_id, mk_date, payload, status,
                       last_attempt_at, acked_at
                FROM delivery
                """
            )
            c.execute("DROP TABLE delivery")
            c.execute("ALTER TABLE delivery_new RENAME TO delivery")
            c.execute("COMMIT")
        except sqlite3.Error:
            c.execute("ROLLBACK")
            raise

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def get(self, event_id: str) -> DeliveryRow | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM delivery WHERE destination=? AND event_id=?",
                (self.destination, event_id),
            ).fetchone()
        if row is None:
            return None
        return DeliveryRow(
            event_id=row["event_id"],
            mk_date=row["mk_date"],
            payload=json.loads(row["payload"]),
            status=row["status"],
            last_attempt_at=row["last_attempt_at"],
            acked_at=row["acked_at"],
            nack_reason=row["nack_reason"],
            nacked_at=row["nacked_at"],
        )

    def status(self, rec: dict) -> str | None:
        row = self.get(rec["event_id"])
        return row.status if row else None

    def save_pending(self, rec: dict, now: float) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO delivery
                  (destination, event_id, mk_date, payload, status, last_attempt_at, acked_at)
                VALUES (?, ?, ?, ?, 'pending', ?, NULL)
                ON CONFLICT(destination, event_id)
                  DO UPDATE SET last_attempt_at=excluded.last_attempt_at
                """,
                (self.destination, rec["event_id"], rec["mk_date"], json.dumps(rec), now),
            )

    def touch_pending(self, event_id: str, now: float) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE delivery SET last_attempt_at=? WHERE destination=? AND event_id=?",
                (now, self.destination, event_id),
            )

    def mark_acked(self, event_id: str, *, committed_at: str | None) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE delivery SET status='acked', acked_at=? WHERE destination=? AND event_id=?",
                (committed_at, self.destination, event_id),
            )

    def mark_legacy(self, rec: dict) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO delivery
                  (destination, event_id, mk_date, payload, status, last_attempt_at, acked_at)
                VALUES (?, ?, ?, ?, 'legacy_unverified', NULL, NULL)
                ON CONFLICT(destination, event_id) DO UPDATE SET status='legacy_unverified'
                """,
                (self.destination, rec["event_id"], rec["mk_date"], json.dumps(rec)),
            )

    def mark_nacked(self, event_id: str, *, reason: str, failed_at: str | None) -> bool:
        """pending の行だけを nacked にする(acked/legacy を上書きしない)。

        更新できたら True。行が無い/既に pending でなければ False。
        """
        with self._conn() as c:
            cur = c.execute(
                """
                UPDATE delivery SET status='nacked', nack_reason=?, nacked_at=?
                WHERE destination=? AND event_id=? AND status='pending'
                """,
                (reason, failed_at, self.destination, event_id),
            )
            return cur.rowcount == 1


class AckSession:
    """QoS2で presence/record を publish し、<topic>/ack と <topic>/nack を待つ。

    SUBACKが来るまでは publish しない。切断→再接続時は再購読し、新しいSUBACK
    が来るまでまた止まる。retained ACK/NACKは無視する。ack側のSUBACKが拒否
    (0x80)されたら送信自体を止める。nack側のみ拒否された場合は、nackを読め
    ないまま現行(nack対応前)と同じ動作にフォールバックする。
    """

    def __init__(self, client: Any, base_topic: str, store: DeliveryStore,
                 timeout: float = ACK_WAIT_SECONDS, resend_after: float = RESEND_AFTER_SECONDS):
        self._client = client
        self._base_topic = base_topic
        self._ack_topic = base_topic + "/ack"
        self._nack_topic = base_topic + "/nack"
        self._store = store
        self._timeout = timeout
        self._resend_after = resend_after
        self._subscribed = False
        self._nack_enabled = False
        self._expected_mid: int | None = None
        client.on_connect = self._on_connect
        client.on_subscribe = self._on_subscribe
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, rc) -> None:
        self._subscribed = False
        self._nack_enabled = False
        _rc, mid = self._client.subscribe([(self._ack_topic, 2), (self._nack_topic, 2)])
        self._expected_mid = mid

    def _on_subscribe(self, client, userdata, mid, granted_qos) -> None:
        if mid != self._expected_mid:
            return
        granted = list(granted_qos)
        ack_ok = len(granted) >= 1 and granted[0] != SUBACK_FAILURE
        self._nack_enabled = len(granted) >= 2 and granted[1] != SUBACK_FAILURE
        self._subscribed = ack_ok

    def _on_disconnect(self, client, userdata, rc) -> None:
        self._subscribed = False
        self._nack_enabled = False

    def _on_message(self, client, userdata, msg) -> None:
        if msg.retain:
            return
        if msg.topic == self._ack_topic:
            self._handle_ack(msg.payload)
        elif msg.topic == self._nack_topic:
            self._handle_nack(msg.payload)

    def _handle_ack(self, payload: bytes) -> None:
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            return
        event_id = data.get("event_id")
        if not event_id:
            return
        row = self._store.get(event_id)
        if row is None or row.status != "pending":
            return
        if row.mk_date != data.get("mk_date_committed"):
            return
        self._store.mark_acked(event_id, committed_at=data.get("committed_at"))

    def _handle_nack(self, payload: bytes) -> None:
        if not self._nack_enabled:
            return
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            return
        event_id = data.get("event_id")
        if not event_id:
            return
        row = self._store.get(event_id)
        if row is None or row.status != "pending":
            return
        self._store.mark_nacked(
            event_id,
            reason=str(data.get("reason") or ""),
            failed_at=data.get("failed_at_iso") or None,
        )

    def send(self, rec: dict, *, now: float | None = None) -> SendOutcome:
        if not self._subscribed:
            return SendOutcome.PENDING
        now = time.time() if now is None else now
        existing = self._store.get(rec["event_id"])
        if existing is None:
            self._store.save_pending(rec, now)
        else:
            if existing.status == "legacy_unverified":
                return SendOutcome.PENDING
            if existing.payload != rec:
                raise ValueError(f"content mismatch for event_id={rec['event_id']!r}")
            if existing.status == "acked":
                return SendOutcome.ACKED
            if existing.status == "nacked":
                return SendOutcome.NACKED
            if existing.last_attempt_at is not None and (
                now - existing.last_attempt_at
            ) < self._resend_after:
                return SendOutcome.PENDING
            self._store.touch_pending(rec["event_id"], now)
        return self._publish_and_wait(rec)

    def _publish_and_wait(self, rec: dict) -> SendOutcome:
        info = self._client.publish(self._base_topic, json.dumps(rec), 2)
        if getattr(info, "rc", 0) != 0:
            return SendOutcome.PENDING
        deadline = time.monotonic() + self._timeout
        while True:
            row = self._store.get(rec["event_id"])
            if row is not None and row.status == "acked":
                return SendOutcome.ACKED
            if row is not None and row.status == "nacked":
                return SendOutcome.NACKED
            if time.monotonic() >= deadline:
                return SendOutcome.PENDING
            time.sleep(min(0.01, self._timeout))


@dataclass
class DeliveryResult:
    acked: int = 0
    invalid: int = 0
    pending: int = 0
    nacked: int = 0
    complete: bool = False
    nacked_rows: list[tuple[int, str]] = field(default_factory=list)


def deliver_file(path: Path, transport: AckSession, parse_fn,
                  *, max_seconds: float | None = None) -> DeliveryResult:
    """CSVを1行ずつtransportへ流す。末尾に改行のない行(書き込み途中の可能性)は
    処理せず、次回の走査に残す。読み取り前後でファイル属性が変わっていたら
    complete=False(archiveへは進めない)。nacked(恒久失敗)行はpendingに数えない
    ので、他の全行がacked/nackedならcompleteになる。
    """
    started = time.monotonic()
    stat_before = path.stat()
    text = path.read_text(encoding="utf-8")
    ends_with_newline = text.endswith("\n") or text == ""
    lines = text.split("\n")[:-1]

    acked = invalid = pending = nacked = 0
    nacked_rows: list[tuple[int, str]] = []
    truncated = False
    for line_no, raw in enumerate(lines, start=1):
        if max_seconds is not None and time.monotonic() - started > max_seconds:
            truncated = True
            break
        line = raw.strip()
        if not line:
            continue
        rec = parse_fn(line)
        if rec is None:
            invalid += 1
            continue
        outcome = transport.send(rec)
        if outcome == SendOutcome.ACKED:
            acked += 1
        elif outcome == SendOutcome.NACKED:
            nacked += 1
            nacked_rows.append((line_no, rec["event_id"]))
        else:
            pending += 1

    stat_after = path.stat()
    unchanged = (
        stat_before.st_size == stat_after.st_size
        and stat_before.st_mtime_ns == stat_after.st_mtime_ns
    )
    complete = not truncated and ends_with_newline and unchanged and invalid == 0 and pending == 0
    return DeliveryResult(
        acked=acked, invalid=invalid, pending=pending, nacked=nacked,
        complete=complete, nacked_rows=nacked_rows,
    )
