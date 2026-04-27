import os
import re
from pathlib import Path
from typing import Any

import yaml

_ENV_RE = re.compile(r"\$\{([^}]+)\}")


class ConfigError(Exception):
    pass


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"top-level YAML must be a mapping in {path}")
    return data


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            var = match.group(1)
            if var not in os.environ:
                raise ConfigError(f"environment variable not set: {var}")
            return os.environ[var]
        return _ENV_RE.sub(replace, value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


HOSTNAME_FILE = "/etc/host_hostname"

_DETECTOR_REQUIRED = {
    "camera": {"device", "width", "height", "warmup_frames"},
    "inference": {"model_path", "target_fps", "score_threshold", "category"},
    "debounce": {"enter_seconds", "exit_seconds"},
    "mqtt": {"host", "port", "qos", "topic_event", "topic_ack", "client_id_prefix"},
    "retry": {"initial_delay_seconds", "max_delay_seconds", "multiplier"},
    "buffer": {"path", "max_rows"},
}

_DEVICE_REQUIRED = {
    "station": {"sta_no1", "sta_no2", "sta_no3"},
}


def _validate_required(data: dict[str, Any], required: dict[str, set[str]], where: str) -> None:
    for section, keys in required.items():
        if section not in data:
            raise ConfigError(f"{where}: missing required section: {section}")
        if not isinstance(data[section], dict):
            raise ConfigError(f"{where}: section {section} must be a mapping")
        missing = keys - set(data[section].keys())
        if missing:
            raise ConfigError(f"{where}: missing required key(s) {sorted(missing)} in {section}")


def load_detector_config(path: Path) -> dict[str, Any]:
    data = expand_env(load_yaml(path))
    _validate_required(data, _DETECTOR_REQUIRED, str(path))
    return data


def _read_hostname_file() -> str:
    p = Path(HOSTNAME_FILE)
    if not p.exists():
        raise ConfigError(f"device_id is null but hostname file missing: {HOSTNAME_FILE}")
    return p.read_text(encoding="utf-8").strip()


def load_device_config(path: Path) -> dict[str, Any]:
    data = expand_env(load_yaml(path))
    _validate_required(data, _DEVICE_REQUIRED, str(path))
    if data.get("device_id") is None:
        data["device_id"] = _read_hostname_file()
    return data
