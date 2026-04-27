import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from services.detector.src.buffer import BufferRepository, PendingEvent


def _make_event(event_id: str, *, created_at: datetime, status: str = "pending",
                event_type: str = "ENTER", monotonic_ns: int = 0) -> PendingEvent:
    return PendingEvent(
        event_id=event_id,
        event_type=event_type,
        mk_date="20260427120000",
        monotonic_ns=monotonic_ns,
        wall_synced=True,
        score=0.9,
        status=status,
        created_at_iso=created_at.isoformat(timespec="milliseconds"),
        retry_count=0,
        next_retry_at_iso=None,
        last_publish_at_iso=None,
    )


def test_init_creates_db_with_pragmas(tmp_path: Path):
    repo = BufferRepository(tmp_path / "x.db")
    repo.init()
    with sqlite3.connect(tmp_path / "x.db") as c:
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"


def test_insert_pending_then_query_count(tmp_path: Path):
    repo = BufferRepository(tmp_path / "x.db")
    repo.init()
    repo.insert_pending(_make_event("e1", created_at=datetime.now(UTC)))
    assert repo.count() == 1


def test_insert_pending_idempotent_on_event_id(tmp_path: Path):
    repo = BufferRepository(tmp_path / "x.db")
    repo.init()
    e = _make_event("e1", created_at=datetime.now(UTC))
    repo.insert_pending(e)
    repo.insert_pending(e)  # second call should not raise nor duplicate
    assert repo.count() == 1


def test_mark_sent_then_acked(tmp_path: Path):
    repo = BufferRepository(tmp_path / "x.db")
    repo.init()
    repo.insert_pending(_make_event("e1", created_at=datetime.now(UTC)))
    repo.mark_sent("e1")
    repo.mark_acked("e1")
    rows = list(repo.iter_due_for_retry(now_iso=datetime.now(UTC).isoformat(), status="acked"))
    assert len(rows) == 1


def test_iter_due_for_retry_filters_by_time_and_status(tmp_path: Path):
    repo = BufferRepository(tmp_path / "x.db")
    repo.init()
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    e_due = _make_event("e_due", created_at=now)
    e_due.next_retry_at_iso = (now - timedelta(seconds=5)).isoformat()
    repo.insert_pending(e_due)
    e_future = _make_event("e_future", created_at=now)
    e_future.next_retry_at_iso = (now + timedelta(seconds=60)).isoformat()
    repo.insert_pending(e_future)
    due = [r.event_id for r in repo.iter_due_for_retry(now_iso=now.isoformat(), status="pending")]
    assert due == ["e_due"]


def test_update_retry_metadata_bumps_retry_count(tmp_path: Path):
    repo = BufferRepository(tmp_path / "x.db")
    repo.init()
    now = datetime.now(UTC)
    repo.insert_pending(_make_event("e1", created_at=now))
    repo.update_retry_metadata("e1", retry_count=2, next_retry_at_iso="2026-04-27T12:00:30+00:00")
    row = repo.get("e1")
    assert row.retry_count == 2
    assert row.next_retry_at_iso == "2026-04-27T12:00:30+00:00"


def test_ring_evict_drops_acked_first(tmp_path: Path):
    repo = BufferRepository(tmp_path / "x.db")
    repo.init()
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    for i, status in enumerate(["acked", "acked", "sent", "pending"]):
        e = _make_event(f"e{i}", created_at=now + timedelta(seconds=i), status=status)
        repo.insert_pending(e)
        if status in ("sent", "acked"):
            repo.mark_sent(e.event_id)
        if status == "acked":
            repo.mark_acked(e.event_id)
    deleted = repo.ring_evict(max_rows=2)
    assert deleted == 2
    remaining = {r.event_id for r in repo.all_rows()}
    assert remaining == {"e2", "e3"}  # the two acked rows e0, e1 were dropped first


def test_ring_evict_falls_back_to_pending_when_only_pending_left(tmp_path: Path):
    repo = BufferRepository(tmp_path / "x.db")
    repo.init()
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    for i in range(3):
        repo.insert_pending(_make_event(f"e{i}", created_at=now + timedelta(seconds=i)))
    deleted = repo.ring_evict(max_rows=2)
    assert deleted == 1  # only the oldest pending dropped
