import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox (
  event_id            TEXT PRIMARY KEY,
  event_type          TEXT NOT NULL CHECK(event_type IN ('ENTER','EXIT')),
  mk_date             TEXT,
  monotonic_ns        INTEGER NOT NULL,
  wall_synced         INTEGER NOT NULL,
  device_id           TEXT,
  score               REAL,
  raw_payload         TEXT NOT NULL,
  status              TEXT NOT NULL CHECK(status IN ('received','sent')),
  ssid_at_receive     TEXT,
  profile_at_send     TEXT,
  mk_date_committed   TEXT,
  received_at_iso     TEXT NOT NULL,
  sent_at_iso         TEXT,
  retry_count         INTEGER NOT NULL DEFAULT 0,
  next_retry_at_iso   TEXT,
  last_error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_inbox_status_retry ON inbox(status, next_retry_at_iso);
CREATE INDEX IF NOT EXISTS idx_inbox_received_at ON inbox(received_at_iso);
"""

PRAGMAS = ["PRAGMA journal_mode = WAL", "PRAGMA synchronous = NORMAL"]


@dataclass
class InboxEvent:
    event_id: str
    event_type: str
    mk_date: str | None
    monotonic_ns: int
    wall_synced: bool
    device_id: str | None
    score: float | None
    raw_payload: str
    status: str
    ssid_at_receive: str | None
    profile_at_send: str | None
    mk_date_committed: str | None
    received_at_iso: str
    sent_at_iso: str | None
    retry_count: int
    next_retry_at_iso: str | None
    last_error: str | None


class InboxRepository:
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

    def insert_received(self, e: InboxEvent) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO inbox (event_id, event_type, mk_date, monotonic_ns, wall_synced,
                  device_id, score, raw_payload, status, ssid_at_receive, profile_at_send,
                  mk_date_committed, received_at_iso, sent_at_iso, retry_count,
                  next_retry_at_iso, last_error)
                VALUES (:event_id, :event_type, :mk_date, :monotonic_ns, :wall_synced,
                  :device_id, :score, :raw_payload, :status, :ssid_at_receive, :profile_at_send,
                  :mk_date_committed, :received_at_iso, :sent_at_iso, :retry_count,
                  :next_retry_at_iso, :last_error)
                ON CONFLICT(event_id) DO NOTHING
                """,
                {**asdict(e), "wall_synced": int(e.wall_synced)},
            )

    def mark_sent(self, event_id: str, *, mk_date_committed: str, profile_at_send: str,
                  sent_at_iso: str) -> None:
        with self._conn() as c:
            c.execute(
                """
                UPDATE inbox
                SET status='sent', mk_date_committed=?, profile_at_send=?, sent_at_iso=?
                WHERE event_id=?
                """,
                (mk_date_committed, profile_at_send, sent_at_iso, event_id),
            )

    def update_retry(self, event_id: str, *, retry_count: int, next_retry_at_iso: str,
                     last_error: str) -> None:
        with self._conn() as c:
            c.execute(
                """
                UPDATE inbox
                SET retry_count=?, next_retry_at_iso=?, last_error=?
                WHERE event_id=?
                """,
                (retry_count, next_retry_at_iso, last_error, event_id),
            )

    def get(self, event_id: str) -> InboxEvent | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM inbox WHERE event_id=?", (event_id,)).fetchone()
        return self._row_to_event(row) if row else None

    def iter_received_due(self, *, now_iso: str) -> Iterator[InboxEvent]:
        with self._conn() as c:
            cur = c.execute(
                """
                SELECT * FROM inbox
                WHERE status='received'
                  AND (next_retry_at_iso IS NULL OR next_retry_at_iso <= ?)
                ORDER BY received_at_iso ASC
                """,
                (now_iso,),
            )
            for row in cur.fetchall():
                yield self._row_to_event(row)

    def iter_sent_without_ack(self, *, now_iso: str) -> Iterator[InboxEvent]:
        # ACK-resend candidates: rows whose status='sent' (the bridge restart case).
        # `now_iso` is reserved for future age-based filtering; not currently used.
        del now_iso
        with self._conn() as c:
            cur = c.execute(
                "SELECT * FROM inbox WHERE status='sent' ORDER BY received_at_iso ASC"
            )
            for row in cur.fetchall():
                yield self._row_to_event(row)

    def all_rows(self) -> Iterator[InboxEvent]:
        with self._conn() as c:
            cur = c.execute("SELECT * FROM inbox ORDER BY received_at_iso ASC")
            for row in cur.fetchall():
                yield self._row_to_event(row)

    def count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM inbox").fetchone()[0]

    def ring_evict(self, *, max_rows: int) -> int:
        deleted = 0
        with self._conn() as c:
            current = c.execute("SELECT COUNT(*) FROM inbox").fetchone()[0]
            to_delete = max(0, current - max_rows)
            for status in ("sent", "received"):
                if to_delete == 0:
                    break
                cur = c.execute(
                    """
                    SELECT event_id FROM inbox
                    WHERE status=?
                    ORDER BY received_at_iso ASC
                    LIMIT ?
                    """,
                    (status, to_delete),
                )
                ids = [r[0] for r in cur.fetchall()]
                if ids:
                    c.executemany("DELETE FROM inbox WHERE event_id=?", [(i,) for i in ids])
                    deleted += len(ids)
                    to_delete -= len(ids)
        return deleted

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> InboxEvent:
        return InboxEvent(
            event_id=row["event_id"],
            event_type=row["event_type"],
            mk_date=row["mk_date"],
            monotonic_ns=row["monotonic_ns"],
            wall_synced=bool(row["wall_synced"]),
            device_id=row["device_id"],
            score=row["score"],
            raw_payload=row["raw_payload"],
            status=row["status"],
            ssid_at_receive=row["ssid_at_receive"],
            profile_at_send=row["profile_at_send"],
            mk_date_committed=row["mk_date_committed"],
            received_at_iso=row["received_at_iso"],
            sent_at_iso=row["sent_at_iso"],
            retry_count=row["retry_count"],
            next_retry_at_iso=row["next_retry_at_iso"],
            last_error=row["last_error"],
        )
