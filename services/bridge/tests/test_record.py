import json

import pytest

from services.bridge.src.record import RecordPayload, parse_record_payload


def _raw(d: dict) -> bytes:
    return json.dumps(d).encode("utf-8")


def _valid() -> dict:
    return {
        "event_id": "abc123",
        "mk_date": "20260610173000",
        "sta_no1": "100",
        "sta_no2": "200",
        "sta_no3": "300",
        "t1_status": 3,
        "device_id": "child-01",
        "schema_version": 1,
    }


def test_parse_valid_record():
    r = parse_record_payload(_raw(_valid()))
    assert r == RecordPayload(
        event_id="abc123",
        mk_date="20260610173000",
        sta_no1="100",
        sta_no2="200",
        sta_no3="300",
        t1_status=3,
        device_id="child-01",
        schema_version=1,
    )


def test_sta_no_coerced_to_str_and_t1_status_to_int():
    d = _valid()
    d["sta_no1"] = 100      # numeric in JSON
    d["t1_status"] = "3"    # stringy int
    r = parse_record_payload(_raw(d))
    assert r.sta_no1 == "100"
    assert r.t1_status == 3


def test_missing_required_key_raises():
    required = ("event_id", "mk_date", "sta_no1", "sta_no2", "sta_no3",
                "t1_status", "schema_version")
    for k in required:
        d = _valid()
        del d[k]
        with pytest.raises(ValueError, match="missing"):
            parse_record_payload(_raw(d))


def test_device_id_optional():
    d = _valid()
    del d["device_id"]
    r = parse_record_payload(_raw(d))
    assert r.device_id is None


def test_bad_mk_date_raises():
    d = _valid()
    d["mk_date"] = "2026-06-10"  # not YYYYMMDDhhmmss
    with pytest.raises(ValueError, match="mk_date"):
        parse_record_payload(_raw(d))


def test_invalid_json_raises():
    with pytest.raises(ValueError, match="JSON"):
        parse_record_payload(b"{not json")
