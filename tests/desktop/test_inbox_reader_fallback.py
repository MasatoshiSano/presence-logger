"""record_inbox の docker-exec フォールバック（root所有WAL DBをpiが直読みできない場合）。

本番の DB は root 所有 & WAL のため ?mode=ro 直読みが OperationalError になり、
bridge コンテナ経由に切り替わる。ここでは docker を使わず、runner を差し替えて
コンテナ出力の解析とフォールバック経路を検証する。
"""
import sqlite3

from pipeline_monitor.inbox_reader import RecordInboxReader, parse_container_output

_SAMPLE = (
    "CNT\treceived\t71\n"
    "CNT\tsent\t49\n"
    "DEV\tpizero2w\t120\t20260717091000\n"
    "ROW\t9a160dca1e\tpizero2w\t20260717090000\treceived\t0\t\t2026-07-17T09:00:00Z\t\n"
    "ROW\t532e09d250\tpizero2w\t20260717091000\tsent\t2\tORA-12514\t"
    "2026-07-17T09:10:00Z\t2026-07-17T09:10:05Z\n"
)


def test_parse_container_output_counts_and_rows():
    view = parse_container_output(_SAMPLE)
    assert view.received == 71
    assert view.sent == 49
    assert view.total == 120
    assert len(view.devices) == 1
    assert view.devices[0].device_id == "pizero2w"
    assert view.devices[0].count == 120
    assert view.devices[0].last_mk_date == "20260717091000"
    assert view.rows[0].event_id == "9a160dca1e"
    assert view.rows[0].device_id == "pizero2w"
    assert view.rows[0].last_error is None       # empty field -> None
    assert view.rows[0].sent_at_iso is None
    assert view.rows[1].status == "sent"
    assert view.rows[1].retry_count == 2
    assert view.rows[1].last_error == "ORA-12514"
    assert view.rows[1].sent_at_iso == "2026-07-17T09:10:05Z"


def test_read_falls_back_to_container_on_operational_error(tmp_path, monkeypatch):
    # a real file so the FileNotFoundError guard passes...
    db = tmp_path / "buf.db"
    db.write_bytes(b"")
    captured = {}

    def fake_runner(cmd):
        captured["cmd"] = cmd
        return _SAMPLE

    reader = RecordInboxReader(str(db), bridge_container="presence-bridge",
                               runner=fake_runner)

    # ...but force the direct read to raise OperationalError like a root/WAL DB would.
    def boom(_limit):
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(reader, "_read_direct", boom)

    view = reader.read(limit=30)
    assert view.received == 71
    assert view.sent == 49
    assert "docker" in captured["cmd"]
    assert "presence-bridge" in captured["cmd"]
    assert str(db) in captured["cmd"]


def test_missing_db_still_raises_before_fallback(tmp_path):
    import pytest
    reader = RecordInboxReader(str(tmp_path / "nope.db"), runner=lambda cmd: "")
    with pytest.raises(FileNotFoundError):
        reader.read()
