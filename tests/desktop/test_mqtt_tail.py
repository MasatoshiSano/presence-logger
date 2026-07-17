from pipeline_monitor.mqtt_tail import MqttTail


def _tail(**kw):
    # fixed clock so ts is deterministic
    return MqttTail("h", 1883, clock=lambda: 1000.0, **kw)


def test_ingest_v_line_splits_topic_and_payload():
    t = _tail()
    t.ingest_line('presence/status/zero2 online')
    msgs = t.messages()
    assert len(msgs) == 1
    assert msgs[0].kind == "status"
    assert msgs[0].device_id == "zero2"


def test_ingest_record_line_with_spaces_in_json():
    t = _tail()
    line = (
        'presence/record {"event_id": "abc", "device_id": "zero2", '
        '"t1_status": 1, "mk_date": "20260717090000", "sta_no1":"1",'
        '"sta_no2":"2","sta_no3":"3","schema_version":1}'
    )
    t.ingest_line(line)
    assert t.messages()[0].event_id == "abc"
    assert "abc" in t.event_ids()


def test_ring_buffer_bounds_length():
    t = _tail(maxlen=2)
    for i in range(5):
        t.ingest_line(f'presence/status/dev{i} online')
    msgs = t.messages()
    assert len(msgs) == 2
    assert msgs[-1].device_id == "dev4"     # newest kept


def test_blank_line_ignored():
    t = _tail()
    t.ingest_line("")
    t.ingest_line("   ")
    assert t.messages() == []


def test_event_ids_excludes_none():
    t = _tail()
    t.ingest_line('presence/heartbeat/zero2 {"uptime_s":1}')  # no event_id
    assert t.event_ids() == set()
