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


def test_mark_failed_stops_it_being_due(tmp_path):
    r = _repo(tmp_path)
    r.insert_received(_evt(event_id="r1"))
    r.mark_failed("r1", failed_at_iso="2026-06-10T17:31:00+09:00",
                  last_error="ORA-1: unique constraint violated")
    assert list(r.iter_received_due(now_iso="2026-06-10T18:00:00+09:00")) == []
    assert r.count() == 1  # 残る(消えない)。ただし二度と送信対象にならない


def test_migration_adds_failed_status_to_pre_existing_db(tmp_path):
    """failed列が無い旧スキーマのDB(本番相当)に対しても init() が安全に移行できる。"""
    import sqlite3

    db = tmp_path / "old.db"
    old_schema = """
    CREATE TABLE record_inbox (
      event_id TEXT PRIMARY KEY, mk_date TEXT NOT NULL,
      sta_no1 TEXT NOT NULL, sta_no2 TEXT NOT NULL, sta_no3 TEXT NOT NULL,
      t1_status INTEGER NOT NULL, device_id TEXT, raw_payload TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('received','sent')),
      received_at_iso TEXT NOT NULL, sent_at_iso TEXT, mk_date_committed TEXT,
      retry_count INTEGER NOT NULL DEFAULT 0, next_retry_at_iso TEXT, last_error TEXT
    );
    """
    conn = sqlite3.connect(db)
    conn.executescript(old_schema)
    conn.execute(
        "INSERT INTO record_inbox (event_id, mk_date, sta_no1, sta_no2, sta_no3, "
        "t1_status, device_id, raw_payload, status, received_at_iso) "
        "VALUES ('old1','20260610173000','100','200','300',3,'child-01','{}','received',"
        "'2026-06-10T17:30:00+09:00')"
    )
    conn.commit()
    conn.close()

    r = RecordInboxRepository(db)
    r.init()  # 移行が走るはず
    assert r.count() == 1  # 既存行は保持される
    r.mark_failed("old1", failed_at_iso="2026-06-10T18:00:00+09:00", last_error="ORA-1")
    assert list(r.iter_received_due(now_iso="2026-06-10T19:00:00+09:00")) == []

    r.init()  # 二重に呼んでも壊れない(冪等)
    assert r.count() == 1
