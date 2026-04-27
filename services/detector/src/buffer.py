import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_events (
  event_id            TEXT PRIMARY KEY,
  event_type          TEXT NOT NULL CHECK(event_type IN ('ENTER','EXIT')),
  mk_date             TEXT,
  monotonic_ns        INTEGER NOT NULL,
  wall_synced         INTEGER NOT NULL DEFAULT 0,
  score               REAL,
  status              TEXT NOT NULL CHECK(status IN ('pending','sent','acked')),
  created_at_iso      TEXT NOT NULL,
  retry_count         INTEGER NOT NULL DEFAULT 0,
  next_retry_at_iso   TEXT,
  last_publish_at_iso TEXT
);
CREATE INDEX IF NOT EXISTS idx_pending_events_status_retry
  ON pending_events(status, next_retry_at_iso);
CREATE INDEX IF NOT EXISTS idx_pending_events_created_at
  ON pending_events(created_at_iso);
"""

PRAGMAS = ["PRAGMA journal_mode = WAL", "PRAGMA synchronous = NORMAL"]


@dataclass
class PendingEvent:
    event_id: str
    event_type: str
    mk_date: str | None
    monotonic_ns: int
    wall_synced: bool
    score: float | None
    status: str
    created_at_iso: str
    retry_count: int
    next_retry_at_iso: str | None
    last_publish_at_iso: str | None


class BufferRepository:
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

    def insert_pending(self, e: PendingEvent) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO pending_events (event_id, event_type, mk_date, monotonic_ns,
                  wall_synced, score, status, created_at_iso, retry_count,
                  next_retry_at_iso, last_publish_at_iso)
                VALUES (:event_id, :event_type, :mk_date, :monotonic_ns,
                  :wall_synced, :score, :status, :created_at_iso, :retry_count,
                  :next_retry_at_iso, :last_publish_at_iso)
                ON CONFLICT(event_id) DO NOTHING
                """,
                {**asdict(e), "wall_synced": int(e.wall_synced)},
            )

    def mark_sent(self, event_id: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE pending_events SET status='sent' WHERE event_id=?", (event_id,))

    def mark_acked(self, event_id: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE pending_events SET status='acked' WHERE event_id=?", (event_id,))

    def update_retry_metadata(
        self, event_id: str, *, retry_count: int, next_retry_at_iso: str
    ) -> None:
        with self._conn() as c:
            c.execute(
                """
                UPDATE pending_events
                SET retry_count=?, next_retry_at_iso=?, last_publish_at_iso=?
                WHERE event_id=?
                """,
                (retry_count, next_retry_at_iso, next_retry_at_iso, event_id),
            )

    def get(self, event_id: str) -> PendingEvent | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM pending_events WHERE event_id=?", (event_id,)).fetchone()
        return self._row_to_event(row) if row else None

    def iter_due_for_retry(self, *, now_iso: str, status: str) -> Iterator[PendingEvent]:
        with self._conn() as c:
            cur = c.execute(
                """
                SELECT * FROM pending_events
                WHERE status = ?
                  AND (next_retry_at_iso IS NULL OR next_retry_at_iso <= ?)
                ORDER BY created_at_iso ASC
                """,
                (status, now_iso),
            )
            for row in cur.fetchall():
                yield self._row_to_event(row)

    def all_rows(self) -> Iterator[PendingEvent]:
        with self._conn() as c:
            cur = c.execute("SELECT * FROM pending_events ORDER BY created_at_iso ASC")
            for row in cur.fetchall():
                yield self._row_to_event(row)

    def count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM pending_events").fetchone()[0]

    def ring_evict(self, *, max_rows: int) -> int:
        """Delete oldest rows down to `max_rows`. Prefer acked, then sent, then pending."""
        deleted = 0
        with self._conn() as c:
            current = c.execute("SELECT COUNT(*) FROM pending_events").fetchone()[0]
            to_delete = max(0, current - max_rows)
            for status in ("acked", "sent", "pending"):
                if to_delete == 0:
                    break
                cur = c.execute(
                    """
                    SELECT event_id FROM pending_events
                    WHERE status=?
                    ORDER BY created_at_iso ASC
                    LIMIT ?
                    """,
                    (status, to_delete),
                )
                ids = [r[0] for r in cur.fetchall()]
                if ids:
                    c.executemany(
                        "DELETE FROM pending_events WHERE event_id=?",
                        [(i,) for i in ids],
                    )
                    deleted += len(ids)
                    to_delete -= len(ids)
        return deleted

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> PendingEvent:
        return PendingEvent(
            event_id=row["event_id"],
            event_type=row["event_type"],
            mk_date=row["mk_date"],
            monotonic_ns=row["monotonic_ns"],
            wall_synced=bool(row["wall_synced"]),
            score=row["score"],
            status=row["status"],
            created_at_iso=row["created_at_iso"],
            retry_count=row["retry_count"],
            next_retry_at_iso=row["next_retry_at_iso"],
            last_publish_at_iso=row["last_publish_at_iso"],
        )
