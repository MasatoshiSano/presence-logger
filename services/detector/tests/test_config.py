from pathlib import Path

import pytest

from services.detector.src.config import (
    ConfigError,
    expand_env,
    load_detector_config,
    load_device_config,
    load_yaml,
)


def test_load_yaml_returns_dict(tmp_path: Path):
    p = tmp_path / "x.yaml"
    p.write_text("camera:\n  device: /dev/video0\n  width: 640\n")
    result = load_yaml(p)
    assert result == {"camera": {"device": "/dev/video0", "width": 640}}


def test_load_yaml_missing_file_raises(tmp_path: Path):
    with pytest.raises(ConfigError, match="not found"):
        load_yaml(tmp_path / "missing.yaml")


def test_expand_env_replaces_placeholder(monkeypatch):
    monkeypatch.setenv("MY_PASS", "secret123")
    assert expand_env({"password": "${MY_PASS}"}) == {"password": "secret123"}


def test_expand_env_recurses_into_nested_dict(monkeypatch):
    monkeypatch.setenv("DB_HOST", "10.0.0.1")
    src = {"oracle": {"host": "${DB_HOST}", "port": 1521}}
    assert expand_env(src) == {"oracle": {"host": "10.0.0.1", "port": 1521}}


def test_expand_env_unresolved_var_raises(monkeypatch):
    monkeypatch.delenv("NONEXISTENT", raising=False)
    with pytest.raises(ConfigError, match="NONEXISTENT"):
        expand_env({"x": "${NONEXISTENT}"})


def test_expand_env_literal_string_unchanged():
    assert expand_env({"x": "literal"}) == {"x": "literal"}


def test_load_detector_config_validates_required_keys(tmp_path: Path):
    p = tmp_path / "detector.yaml"
    p.write_text("camera:\n  device: /dev/video0\n")  # missing inference, debounce, mqtt, etc.
    with pytest.raises(ConfigError, match="missing required key"):
        load_detector_config(p)


def test_load_detector_config_returns_full_dict(tmp_path: Path):
    p = tmp_path / "detector.yaml"
    p.write_text(
        "camera: {device: /dev/video0, width: 640, height: 480, warmup_frames: 5}\n"
        "inference: {model_path: /opt/m.tflite, target_fps: 1.5, score_threshold: 0.5,"
        " category: person}\n"
        "debounce: {enter_seconds: 3.0, exit_seconds: 3.0}\n"
        "mqtt: {host: mosquitto, port: 1883, qos: 2, topic_event: presence/event,"
        " topic_ack: presence/event/ack, client_id_prefix: x}\n"
        "retry: {initial_delay_seconds: 5, max_delay_seconds: 600, multiplier: 3}\n"
        "buffer: {path: /tmp/x.db, max_rows: 100000}\n"
    )
    cfg = load_detector_config(p)
    assert cfg["camera"]["device"] == "/dev/video0"
    assert cfg["debounce"]["enter_seconds"] == 3.0


def test_load_device_config_validates_station(tmp_path: Path):
    p = tmp_path / "device.yaml"
    p.write_text("device_id: foo\nstation:\n  sta_no1: '001'\n")  # missing sta_no2/3
    with pytest.raises(ConfigError, match="sta_no2"):
        load_device_config(p)


def test_load_device_config_resolves_hostname_when_null(tmp_path: Path, monkeypatch):
    p = tmp_path / "device.yaml"
    p.write_text(
        "device_id: null\nstation: {sta_no1: '001', sta_no2: 'A', sta_no3: '01'}\n"
    )
    hostname_file = tmp_path / "hostname"
    hostname_file.write_text("rpi5-test-01\n")
    monkeypatch.setattr("services.detector.src.config.HOSTNAME_FILE", str(hostname_file))
    cfg = load_device_config(p)
    assert cfg["device_id"] == "rpi5-test-01"


def test_load_device_config_keeps_explicit_device_id(tmp_path: Path):
    p = tmp_path / "device.yaml"
    p.write_text(
        "device_id: my-explicit-id\n"
        "station: {sta_no1: '001', sta_no2: 'A', sta_no3: '01'}\n"
    )
    cfg = load_device_config(p)
    assert cfg["device_id"] == "my-explicit-id"
