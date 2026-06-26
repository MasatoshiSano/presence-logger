"""Parser for child-Pi "record" messages (presence/record).

Unlike presence events (ENTER/EXIT), a record carries a fully-formed Oracle row:
the timestamp, all three station numbers and the T1_STATUS come from the child's
CSV line, e.g.  20260610173000,100,200,300,3
The bridge writes these values straight to Oracle (no ENTER/EXIT merge, no
profile station lookup). Wire format is JSON; one message per CSV row.
"""
import json
import re
from dataclasses import dataclass

REQUIRED_RECORD_KEYS = (
    "event_id", "mk_date", "sta_no1", "sta_no2", "sta_no3", "t1_status",
    "schema_version",
)

_MK_DATE_RE = re.compile(r"^\d{14}$")  # YYYYMMDDhhmmss


@dataclass(frozen=True)
class RecordPayload:
    event_id: str
    mk_date: str
    sta_no1: str
    sta_no2: str
    sta_no3: str
    t1_status: int
    device_id: str | None
    schema_version: int


def parse_record_payload(raw: bytes) -> RecordPayload:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"invalid JSON payload: {e}") from e
    missing = [k for k in REQUIRED_RECORD_KEYS if k not in data]
    if missing:
        raise ValueError(f"payload missing required keys: {missing}")
    mk_date = str(data["mk_date"])
    if not _MK_DATE_RE.match(mk_date):
        raise ValueError(f"mk_date must be YYYYMMDDhhmmss (14 digits), got {mk_date!r}")
    try:
        t1_status = int(data["t1_status"])
        schema_version = int(data["schema_version"])
    except (TypeError, ValueError) as e:
        raise ValueError(f"t1_status/schema_version must be int: {e}") from e
    return RecordPayload(
        event_id=str(data["event_id"]),
        mk_date=mk_date,
        sta_no1=str(data["sta_no1"]),
        sta_no2=str(data["sta_no2"]),
        sta_no3=str(data["sta_no3"]),
        t1_status=t1_status,
        device_id=(str(data["device_id"]) if data.get("device_id") is not None else None),
        schema_version=schema_version,
    )
