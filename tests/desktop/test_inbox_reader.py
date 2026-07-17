import sqlite3

import pytest
from pipeline_monitor.inbox_reader import RecordInboxReader

_SCHEMA = """
CREATE TABLE record_inbox (
  event_id TEXT PRIMARY KEY, mk_date TEXT NOT NULL,
  sta_no1 TEXT, sta_no2 TEXT, sta_no3 TEXT, t1_status INTEGER,
  device_id TEXT, raw_payload TEXT NOT NULL,
  status TEXT NOT NULL, received_at_iso TEXT NOT NULL, sent_at_iso TEXT,
  mk_date_committed TEXT, retry_count INTEGER NOT NULL DEFAULT 0,
  next_retry_at_iso TEXT, last_error TEXT
);
"""


def _make_db(path, rows):
    c = sqlite3.connect(path)
    c.executescript(_SCHEMA)
    c.executemany(
        "INSERT INTO record_inbox (event_id, mk_date, sta_no1, sta_no2, sta_no3, "
        "t1_status, device_id, raw_payload, status, received_at_iso, sent_at_iso, "
        "retry_count, last_error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    c.commit()
    c.close()


def test_read_counts_and_orders_recent_first(tmp_path):
    db = tmp_path / "buf.db"
    _make_db(db, [
        ("e1", "20260717090000", "1", "2", "3", 1, "zero2", "{}", "sent",
         "2026-07-17T09:00:00Z", "2026-07-17T09:00:01Z", 0, None),
        ("e2", "20260717091000", "1", "2", "3", 2, "zero2", "{}", "received",
         "2026-07-17T09:10:00Z", None, 3, "ORA-12514"),
    ])
    view = RecordInboxReader(str(db)).read(limit=30)
    assert view.total == 2
    assert view.sent == 1
    assert view.received == 1
    assert view.rows[0].event_id == "e2"       # most recent first
    assert view.rows[0].status == "received"
    assert view.rows[0].retry_count == 3
    assert view.rows[0].last_error == "ORA-12514"


def test_missing_db_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        RecordInboxReader(str(tmp_path / "nope.db")).read()


def test_read_is_readonly(tmp_path):
    db = tmp_path / "buf.db"
    _make_db(db, [("e1", "20260717090000", "1", "2", "3", 1, "z", "{}",
                   "sent", "2026-07-17T09:00:00Z", "2026-07-17T09:00:01Z", 0, None)])
    reader = RecordInboxReader(str(db))
    reader.read()
    # opening ?mode=ro must reject writes
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as ro:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("DELETE FROM record_inbox")
