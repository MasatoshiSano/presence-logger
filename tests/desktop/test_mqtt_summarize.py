from pipeline_monitor.model import MqttMsg
from pipeline_monitor.mqtt_tail import collapse_heartbeats, mqtt_summarize

NOW = 1_700_000_000.0


def _msg(kind, device_id, ts, summary=None):
    return MqttMsg(ts=ts, topic="t", kind=kind, device_id=device_id,
                    event_id=None, summary=summary or f"{device_id} {kind}", raw="")


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
    assert "STA=100/200/300" in m.summary  # 局番も生ログで見える


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


def test_collapse_heartbeats_groups_consecutive_same_device():
    msgs = [
        _msg("heartbeat", "zero2", NOW),
        _msg("heartbeat", "zero2", NOW + 15),
        _msg("heartbeat", "zero2", NOW + 30),
    ]
    lines = collapse_heartbeats(msgs)
    assert len(lines) == 1
    assert lines[0].dim is True
    assert "x3" in lines[0].text
    assert lines[0].ts == NOW + 30            # 最新のtsを使う


def test_collapse_heartbeats_breaks_on_other_kind_and_device_change():
    msgs = [
        _msg("heartbeat", "zero2", NOW),
        _msg("heartbeat", "zero2", NOW + 15),
        _msg("record", "zero2", NOW + 16, summary="zero2 record!"),
        _msg("heartbeat", "zero3", NOW + 20),
    ]
    lines = collapse_heartbeats(msgs)
    assert len(lines) == 3
    assert lines[0].dim is True
    assert "x2" in lines[0].text
    assert lines[1].dim is False
    assert lines[1].text == "zero2 record!"
    assert lines[2].dim is True
    assert "zero3" in lines[2].text


def test_collapse_heartbeats_single_heartbeat_keeps_original_summary():
    msgs = [_msg("heartbeat", "zero2", NOW, summary="zero2 💓")]
    lines = collapse_heartbeats(msgs)
    assert lines[0].text == "zero2 💓"
    assert lines[0].dim is True
