"""Durable buffer for child-Pi records (presence/record) awaiting Oracle write.

Kept separate from the ENTER/EXIT `inbox` (which has an event_type CHECK and
event-specific columns) so the two data paths stay isolated and we avoid a risky
migration of the live inbox. Records carry their own MK_DATE / STA_NO* /
T1_STATUS, so this table stores those directly. Same durability story as inbox:
buffer on receive, retry until Oracle accepts, then mark sent.
"""
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS record_inbox (
  event_id            TEXT PRIMARY KEY,
  mk_date             TEXT NOT NULL,
  sta_no1             TEXT NOT NULL,
  sta_no2             TEXT NOT NULL,
  sta_no3             TEXT NOT NULL,
  t1_status           INTEGER NOT NULL,
  device_id           TEXT,
  raw_payload         TEXT NOT NULL,
  status              TEXT NOT NULL CHECK(status IN ('received','sent')),
  received_at_iso     TEXT NOT NULL,
  sent_at_iso         TEXT,
  mk_date_committed   TEXT,
  retry_count         INTEGER NOT NULL DEFAULT 0,
  next_retry_at_iso   TEXT,
  last_error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_record_inbox_status_retry
  ON record_inbox(status, next_retry_at_iso);
"""

PRAGMAS = ["PRAGMA journal_mode = WAL", "PRAGMA synchronous = NORMAL"]


@dataclass
class RecordInboxEvent:
    event_id: str
    mk_date: str
    sta_no1: str
    sta_no2: str
    sta_no3: str
    t1_status: int
    device_id: str | None
    raw_payload: str
    status: str
    received_at_iso: str
    sent_at_iso: str | None
    mk_date_committed: str | None
    retry_count: int
    next_retry_at_iso: str | None
    last_error: str | None


class RecordInboxRepository:
    def __init__(self, path: Path | str):
        self.path = str(path)

    def init(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            for p in PRAGMAS:
                c.execute(p)
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def insert_received(self, e: RecordInboxEvent) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO record_inbox (event_id, mk_date, sta_no1, sta_no2, sta_no3,
                  t1_status, device_id, raw_payload, status, received_at_iso, sent_at_iso,
                  mk_date_committed, retry_count, next_retry_at_iso, last_error)
                VALUES (:event_id, :mk_date, :sta_no1, :sta_no2, :sta_no3,
                  :t1_status, :device_id, :raw_payload, :status, :received_at_iso, :sent_at_iso,
                  :mk_date_committed, :retry_count, :next_retry_at_iso, :last_error)
                ON CONFLICT(event_id) DO NOTHING
                """,
                asdict(e),
            )

    def mark_sent(self, event_id: str, *, mk_date_committed: str, sent_at_iso: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE record_inbox SET status='sent', mk_date_committed=?, sent_at_iso=? "
                "WHERE event_id=?",
                (mk_date_committed, sent_at_iso, event_id),
            )

    def update_retry(self, event_id: str, *, retry_count: int, next_retry_at_iso: str,
                     last_error: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE record_inbox SET retry_count=?, next_retry_at_iso=?, last_error=? "
                "WHERE event_id=?",
                (retry_count, next_retry_at_iso, last_error, event_id),
            )

    def iter_received_due(self, *, now_iso: str) -> Iterator[RecordInboxEvent]:
        with self._conn() as c:
            cur = c.execute(
                """
                SELECT * FROM record_inbox
                WHERE status='received'
                  AND (next_retry_at_iso IS NULL OR next_retry_at_iso <= ?)
                ORDER BY received_at_iso ASC
                """,
                (now_iso,),
            )
            for row in cur.fetchall():
                yield self._row(row)

    def count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM record_inbox").fetchone()[0]

    def ring_evict(self, *, max_rows: int) -> int:
        deleted = 0
        with self._conn() as c:
            current = c.execute("SELECT COUNT(*) FROM record_inbox").fetchone()[0]
            to_delete = max(0, current - max_rows)
            for status in ("sent", "received"):
                if to_delete == 0:
                    break
                cur = c.execute(
                    "SELECT event_id FROM record_inbox WHERE status=? "
                    "ORDER BY received_at_iso ASC LIMIT ?",
                    (status, to_delete),
                )
                ids = [r[0] for r in cur.fetchall()]
                if ids:
                    c.executemany(
                        "DELETE FROM record_inbox WHERE event_id=?", [(i,) for i in ids]
                    )
                    deleted += len(ids)
                    to_delete -= len(ids)
        return deleted

    @staticmethod
    def _row(row: sqlite3.Row) -> RecordInboxEvent:
        return RecordInboxEvent(
            event_id=row["event_id"],
            mk_date=row["mk_date"],
            sta_no1=row["sta_no1"],
            sta_no2=row["sta_no2"],
            sta_no3=row["sta_no3"],
            t1_status=row["t1_status"],
            device_id=row["device_id"],
            raw_payload=row["raw_payload"],
            status=row["status"],
            received_at_iso=row["received_at_iso"],
            sent_at_iso=row["sent_at_iso"],
            mk_date_committed=row["mk_date_committed"],
            retry_count=row["retry_count"],
            next_retry_at_iso=row["next_retry_at_iso"],
            last_error=row["last_error"],
        )
