from pipeline_monitor.mqtt_tail import mqtt_summarize

NOW = 1_700_000_000.0


def test_record_message_extracts_ids_and_summary():
    payload = (
        '{"event_id":"abc123def456","mk_date":"20260717092300",'
        '"sta_no1":"100","sta_no2":"200","sta_no3":"300","t1_status":1,'
        '"device_id":"zero2","schema_version":1}'
    )
    m = mqtt_summarize("presence/record", payload, now=NOW)
    assert m.kind == "record"
    assert m.device_id == "zero2"
    assert m.event_id == "abc123def456"
    assert m.ts == NOW
    assert "zero2" in m.summary
    assert "20260717092300" in m.summary or "2026-07-17" in m.summary
    assert "T1=1" in m.summary  # 生の T1_STATUS を表示（ENTER/EXIT には変換しない）


def test_status_message_online_offline():
    on = mqtt_summarize("presence/status/pi-b", "online", now=NOW)
    assert on.kind == "status"
    assert on.device_id == "pi-b"
    assert "online" in on.summary
    off = mqtt_summarize("presence/status/pi-b", "offline", now=NOW)
    assert "offline" in off.summary


def test_heartbeat_message_device_from_topic():
    m = mqtt_summarize("presence/heartbeat/zero2", '{"uptime_s":42}', now=NOW)
    assert m.kind == "heartbeat"
    assert m.device_id == "zero2"


def test_ack_message():
    m = mqtt_summarize("presence/record/ack", '{"event_id":"abc"}', now=NOW)
    assert m.kind == "ack"
    assert m.event_id == "abc"


def test_malformed_record_payload_does_not_crash():
    m = mqtt_summarize("presence/record", "{not json", now=NOW)
    assert m.kind == "record"
    assert m.event_id is None          # couldn't parse
    assert m.raw == "{not json"        # raw preserved


def test_unknown_topic_is_other():
    m = mqtt_summarize("presence/misc/thing", "hi", now=NOW)
    assert m.kind == "other"
