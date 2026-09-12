import json

from services.bridge.src.mqtt_file_log import MqttFileLog


def test_write_appends_json_line(tmp_path):
    path = tmp_path / "child-mqtt.log"
    log = MqttFileLog(str(path), max_bytes=10_000, backup_count=1)
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


def test_empty_path_is_noop():
    log = MqttFileLog("")
    log.write("heartbeat", "zero2", "{}")


def test_rotates_when_over_max_bytes(tmp_path):
    path = tmp_path / "child-mqtt.log"
    log = MqttFileLog(str(path), max_bytes=200, backup_count=2)
    for i in range(40):
        log.write("heartbeat", "zero2", json.dumps({"uptime_s": i}))
    assert path.exists()
    rotated = path.with_name("child-mqtt.log.1")
    assert rotated.exists()
