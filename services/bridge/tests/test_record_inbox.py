from services.bridge.src.record_inbox import RecordInboxEvent, RecordInboxRepository


def _evt(event_id="r1", status="received", **kw) -> RecordInboxEvent:
    base = {
        "event_id": event_id,
        "mk_date": "20260610173000",
        "sta_no1": "100",
        "sta_no2": "200",
        "sta_no3": "300",
        "t1_status": 3,
        "device_id": "child-01",
        "raw_payload": "{}",
        "status": status,
        "received_at_iso": "2026-06-10T17:30:00+09:00",
        "sent_at_iso": None,
        "mk_date_committed": None,
        "retry_count": 0,
        "next_retry_at_iso": None,
        "last_error": None,
    }
    base.update(kw)
    return RecordInboxEvent(**base)


def _repo(tmp_path):
    r = RecordInboxRepository(tmp_path / "rec.db")
    r.init()
    return r


def test_insert_and_count(tmp_path):
    r = _repo(tmp_path)
    r.insert_received(_evt())
    assert r.count() == 1


def test_insert_is_idempotent_on_event_id(tmp_path):
    r = _repo(tmp_path)
    r.insert_received(_evt(event_id="dup"))
    r.insert_received(_evt(event_id="dup", t1_status=9))  # same id, ignored
    assert r.count() == 1
    got = next(r.iter_received_due(now_iso="2026-06-10T18:00:00+09:00"))
    assert got.t1_status == 3  # first write kept


def test_iter_received_due_returns_received_rows(tmp_path):
    r = _repo(tmp_path)
    r.insert_received(_evt(event_id="r1"))
    r.insert_received(_evt(event_id="r2", status="sent"))
    due = list(r.iter_received_due(now_iso="2026-06-10T18:00:00+09:00"))
    assert [e.event_id for e in due] == ["r1"]


def test_mark_sent_flips_status(tmp_path):
    r = _repo(tmp_path)
    r.insert_received(_evt(event_id="r1"))
    r.mark_sent("r1", mk_date_committed="20260610173000", sent_at_iso="2026-06-10T17:31:00+09:00")
    assert list(r.iter_received_due(now_iso="2026-06-10T18:00:00+09:00")) == []
    assert r.count() == 1


def test_update_retry_defers_row(tmp_path):
    r = _repo(tmp_path)
    r.insert_received(_evt(event_id="r1"))
    r.update_retry("r1", retry_count=1, next_retry_at_iso="2026-06-10T19:00:00+09:00",
                   last_error="ORA-123")
    # not due yet at 18:00
    assert list(r.iter_received_due(now_iso="2026-06-10T18:00:00+09:00")) == []
    # due at 19:30
    assert [e.event_id for e in r.iter_received_due(now_iso="2026-06-10T19:30:00+09:00")] == ["r1"]
