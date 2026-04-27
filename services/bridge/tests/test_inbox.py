import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from services.bridge.src.inbox import InboxEvent, InboxRepository


def _evt(
    event_id: str,
    *,
    status: str = "received",
    received_at: datetime | None = None,
) -> InboxEvent:
    received_at = received_at or datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    return InboxEvent(
        event_id=event_id,
        event_type="ENTER",
        mk_date="20260427120000",
        monotonic_ns=1_000_000_000,
        wall_synced=True,
        device_id="rpi-test",
        score=0.9,
        raw_payload='{"event_id":"' + event_id + '"}',
        status=status,
        ssid_at_receive="factory_a_wifi",
        profile_at_send=None,
        mk_date_committed=None,
        received_at_iso=received_at.isoformat(),
        sent_at_iso=None,
        retry_count=0,
        next_retry_at_iso=None,
        last_error=None,
    )


def test_init_uses_wal(tmp_path: Path):
    repo = InboxRepository(tmp_path / "x.db")
    repo.init()
    with sqlite3.connect(tmp_path / "x.db") as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_insert_received_is_idempotent(tmp_path: Path):
    repo = InboxRepository(tmp_path / "x.db")
    repo.init()
    e = _evt("e1")
    repo.insert_received(e)
    repo.insert_received(e)
    assert repo.count() == 1


def test_mark_sent_persists_committed_fields(tmp_path: Path):
    repo = InboxRepository(tmp_path / "x.db")
    repo.init()
    repo.insert_received(_evt("e1"))
    repo.mark_sent(
        "e1",
        mk_date_committed="20260427120002",
        profile_at_send="factory_a_wifi",
        sent_at_iso="2026-04-27T12:00:02+00:00",
    )
    row = repo.get("e1")
    assert row.status == "sent"
    assert row.mk_date_committed == "20260427120002"
    assert row.profile_at_send == "factory_a_wifi"


def test_iter_received_due_filters_by_time(tmp_path: Path):
    repo = InboxRepository(tmp_path / "x.db")
    repo.init()
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    e_due = _evt("e_due")
    e_due.next_retry_at_iso = (now - timedelta(seconds=5)).isoformat()
    e_future = _evt("e_future")
    e_future.next_retry_at_iso = (now + timedelta(seconds=60)).isoformat()
    repo.insert_received(e_due)
    repo.insert_received(e_future)
    due = [r.event_id for r in repo.iter_received_due(now_iso=now.isoformat())]
    assert due == ["e_due"]


def test_iter_sent_without_ack_returns_status_sent_only(tmp_path: Path):
    repo = InboxRepository(tmp_path / "x.db")
    repo.init()
    repo.insert_received(_evt("e1"))
    repo.insert_received(_evt("e2"))
    repo.mark_sent(
        "e2",
        mk_date_committed="20260427120000",
        profile_at_send="x",
        sent_at_iso="2026-04-27T12:00:00+00:00",
    )
    sent_ids = [
        r.event_id for r in repo.iter_sent_without_ack(now_iso="2026-04-27T13:00:00+00:00")
    ]
    assert sent_ids == ["e2"]


def test_update_retry_records_error(tmp_path: Path):
    repo = InboxRepository(tmp_path / "x.db")
    repo.init()
    repo.insert_received(_evt("e1"))
    repo.update_retry(
        "e1",
        retry_count=2,
        next_retry_at_iso="2026-04-27T12:00:30+00:00",
        last_error="ORA-12541",
    )
    row = repo.get("e1")
    assert row.retry_count == 2
    assert row.last_error == "ORA-12541"


def test_ring_evict_drops_sent_before_received(tmp_path: Path):
    repo = InboxRepository(tmp_path / "x.db")
    repo.init()
    base = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    for i, status in enumerate(["sent", "sent", "received", "received"]):
        repo.insert_received(_evt(f"e{i}", status=status, received_at=base + timedelta(seconds=i)))
        if status == "sent":
            repo.mark_sent(f"e{i}", mk_date_committed="x", profile_at_send="p", sent_at_iso="x")
    deleted = repo.ring_evict(max_rows=2)
    assert deleted == 2
    remaining = {r.event_id for r in repo.all_rows()}
    assert remaining == {"e2", "e3"}
