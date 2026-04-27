import json
import logging

from services.detector.src.logging_setup import build_formatter, install_common_fields


def test_formatter_emits_iso_timestamp_with_tz():
    formatter = build_formatter()
    record = logging.LogRecord(
        name="detector.fsm", level=logging.INFO, pathname="x", lineno=1,
        msg="hello", args=(), exc_info=None,
    )
    install_common_fields(record, process="detector", device_id="rpi-01")
    output = formatter.format(record)
    parsed = json.loads(output)
    assert "ts" in parsed
    # ISO 8601 with offset, e.g. 2026-04-27T17:23:45.123+09:00
    assert "T" in parsed["ts"]
    assert ("+" in parsed["ts"]) or ("Z" in parsed["ts"])


def test_formatter_includes_required_common_fields():
    formatter = build_formatter()
    record = logging.LogRecord(
        name="detector.fsm", level=logging.INFO, pathname="x", lineno=1,
        msg="m", args=(), exc_info=None,
    )
    install_common_fields(record, process="detector", device_id="rpi-01")
    parsed = json.loads(formatter.format(record))
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "detector.fsm"
    assert parsed["process"] == "detector"
    assert parsed["device_id"] == "rpi-01"
    assert isinstance(parsed["pid"], int)


def test_formatter_includes_extra_fields():
    formatter = build_formatter()
    record = logging.LogRecord(
        name="detector.fsm", level=logging.INFO, pathname="x", lineno=1,
        msg="m", args=(), exc_info=None,
    )
    install_common_fields(record, process="detector", device_id="rpi-01")
    record.event = "transition"
    record.event_id = "abc-123"
    parsed = json.loads(formatter.format(record))
    assert parsed["event"] == "transition"
    assert parsed["event_id"] == "abc-123"


def test_setup_logging_writes_to_file_and_rotates(tmp_path):
    from services.detector.src.logging_setup import setup_logging
    setup_logging(process="detector", device_id="rpi-test", log_dir=str(tmp_path), level="INFO")
    log = logging.getLogger("detector.test")
    log.info("hello", extra={"event": "test_event", "value": 42})

    log_file = tmp_path / "detector.log"
    assert log_file.exists()
    contents = log_file.read_text(encoding="utf-8").strip()
    line = json.loads(contents.splitlines()[-1])
    assert line["event"] == "test_event"
    assert line["value"] == 42
    assert line["device_id"] == "rpi-test"
    assert line["logger"] == "detector.test"
