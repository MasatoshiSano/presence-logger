from pipeline_monitor.model import MqttMsg
from pipeline_monitor.rollup import child_rollup

TIMEOUT = 60.0


def _rec(dev, mk, ts, eid="e"):
    return MqttMsg(ts=ts, topic="presence/record", kind="record",
                   device_id=dev, event_id=eid, summary="", raw=f'{{"mk_date": "{mk}"}}')


def _hb(dev, ts):
    return MqttMsg(ts=ts, topic=f"presence/heartbeat/{dev}", kind="heartbeat",
                   device_id=dev, event_id=None, summary="", raw="")


def _status(dev, state, ts):
    return MqttMsg(ts=ts, topic=f"presence/status/{dev}", kind="status",
                   device_id=dev, event_id=None, summary="", raw=state)


def test_counts_records_and_latest_mk_per_device():
    now = 1000.0
    msgs = [
        _rec("zero2", "20260717090000", 900.0),
        _rec("zero2", "20260717091000", 950.0),
        _rec("pi-b", "20260717080000", 400.0),
    ]
    stats = {s.device_id: s for s in child_rollup(msgs, now=now, heartbeat_timeout=TIMEOUT)}
    assert stats["zero2"].record_count == 2
    assert stats["zero2"].last_record_mk_date == "20260717091000"  # latest
    assert stats["pi-b"].record_count == 1


def test_state_online_when_recent_heartbeat():
    now = 1000.0
    stats = child_rollup([_hb("zero2", 980.0)], now=now, heartbeat_timeout=TIMEOUT)
    assert stats[0].state == "online"
    assert 0 <= stats[0].last_heartbeat_age <= 30


def test_state_stale_when_heartbeat_old_and_no_offline():
    now = 1000.0
    stats = child_rollup([_hb("zero2", 500.0)], now=now, heartbeat_timeout=TIMEOUT)
    assert stats[0].state == "stale"


def test_state_offline_when_last_will_is_newest():
    now = 1000.0
    msgs = [_hb("zero2", 900.0), _status("zero2", "offline", 950.0)]
    stats = child_rollup(msgs, now=now, heartbeat_timeout=TIMEOUT)
    assert stats[0].state == "offline"


def test_result_sorted_by_device_id():
    now = 1000.0
    msgs = [_rec("zero2", "20260717090000", 900.0), _rec("aaa", "20260717090000", 900.0)]
    stats = child_rollup(msgs, now=now, heartbeat_timeout=TIMEOUT)
    assert [s.device_id for s in stats] == ["aaa", "zero2"]
