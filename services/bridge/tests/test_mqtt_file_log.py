import json

from services.bridge.src.mqtt_file_log import MqttFileLog, resolve_mqtt_log_path


def test_write_appends_json_line(tmp_path):
    path = tmp_path / "child-mqtt.log"
    log = MqttFileLog(str(path), max_bytes=10_000, backup_count=1)
    assert log.enabled
    log.write("heartbeat", "zero2", '{"uptime_s": 15}')
    log.write("status", "zero2", "online")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["kind"] == "heartbeat"
    assert first["device_id"] == "zero2"
    assert first["payload"]["uptime_s"] == 15
    second = json.loads(lines[1])
    assert second["kind"] == "status"
    assert second["payload"] == "online"
    assert "ts" in first
    # millisecond precision so bursts in the same second stay ordered
    assert "." in first["ts"].split("T", 1)[1]


def test_empty_path_is_noop():
    log = MqttFileLog("")
    assert not log.enabled
    log.write("heartbeat", "zero2", "{}")


def test_relative_path_is_disabled():
    log = MqttFileLog("logs/child-mqtt.log")
    assert not log.enabled
    assert log.error
    assert log.path is not None


def test_init_fails_when_parent_is_a_file(tmp_path):
    parent = tmp_path / "not-a-dir"
    parent.write_text("x", encoding="utf-8")
    log = MqttFileLog(str(parent / "child-mqtt.log"))
    assert not log.enabled
    assert log.error
    log.write("heartbeat", "zero2", "{}")
    assert not (parent / "child-mqtt.log").exists()


def test_resolve_mqtt_log_path():
    default = "/var/log/presence-logger/child-mqtt.log"
    assert resolve_mqtt_log_path({}) == default
    assert resolve_mqtt_log_path({"mqtt_log_path": ""}) == ""
    assert resolve_mqtt_log_path({"mqtt_log_path": None}) == ""
    assert resolve_mqtt_log_path({"mqtt_log_path": "/var/log/presence-logger/x.log"}) == (
        "/var/log/presence-logger/x.log"
    )


def test_rotates_when_over_max_bytes(tmp_path):
    path = tmp_path / "child-mqtt.log"
    log = MqttFileLog(str(path), max_bytes=200, backup_count=2)
    for i in range(40):
        log.write("heartbeat", "zero2", json.dumps({"uptime_s": i}))
    assert path.exists()
    rotated = path.with_name("child-mqtt.log.1")
    assert rotated.exists()
