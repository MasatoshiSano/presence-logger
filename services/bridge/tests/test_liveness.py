from services.bridge.src.liveness import (
    OFFLINE,
    ONLINE,
    LivenessTracker,
    device_id_from_topic,
)


def test_device_id_from_topic():
    assert device_id_from_topic("presence/status/child-01", "presence/status/") == "child-01"
    assert device_id_from_topic("presence/heartbeat/hub", "presence/heartbeat/") == "hub"
    # non-matching prefix -> None
    assert device_id_from_topic("other/x", "presence/status/") is None


def test_unknown_until_any_signal():
    t = LivenessTracker(heartbeat_timeout_seconds=60)
    assert t.snapshot(now=100.0) == []


def test_online_status_makes_online():
    t = LivenessTracker(heartbeat_timeout_seconds=60)
    t.record_status("d1", ONLINE, now=100.0)
    snap = {d["device_id"]: d["state"] for d in t.snapshot(now=110.0)}
    assert snap["d1"] == "online"


def test_offline_status_makes_offline():
    t = LivenessTracker(heartbeat_timeout_seconds=60)
    t.record_status("d1", ONLINE, now=100.0)
    t.record_status("d1", OFFLINE, now=120.0)  # LWT fired
    snap = {d["device_id"]: d["state"] for d in t.snapshot(now=121.0)}
    assert snap["d1"] == "offline"


def test_heartbeat_keeps_online_then_goes_stale():
    t = LivenessTracker(heartbeat_timeout_seconds=60)
    t.record_heartbeat("d1", {"uptime_s": 5}, now=100.0)
    # within timeout
    assert {d["device_id"]: d["state"] for d in t.snapshot(now=140.0)}["d1"] == "online"
    # past timeout, no new heartbeat, never got offline -> stale (TCP alive but app silent)
    assert {d["device_id"]: d["state"] for d in t.snapshot(now=200.0)}["d1"] == "stale"


def test_heartbeat_after_offline_means_reconnected():
    t = LivenessTracker(heartbeat_timeout_seconds=60)
    t.record_status("d1", OFFLINE, now=100.0)
    t.record_heartbeat("d1", {}, now=110.0)  # newer signal -> back alive
    assert {d["device_id"]: d["state"] for d in t.snapshot(now=120.0)}["d1"] == "online"


def test_snapshot_reports_age_and_last_heartbeat_payload():
    t = LivenessTracker(heartbeat_timeout_seconds=60)
    t.record_heartbeat("d1", {"uptime_s": 42}, now=100.0)
    d = t.snapshot(now=130.0)[0]
    assert d["device_id"] == "d1"
    assert d["age_seconds"] == 30.0
    assert d["last_heartbeat"] == {"uptime_s": 42}
