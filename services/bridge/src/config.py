import os
import re
from pathlib import Path
from typing import Any

import yaml

_ENV_RE = re.compile(r"\$\{([^}]+)\}")
HOSTNAME_FILE = "/etc/host_hostname"

ALLOWED_CLIENT_MODES = {"thin", "thick", "jdbc"}
ALLOWED_AUTH_MODES = {"basic", "wallet"}
ALLOWED_UNKNOWN_POLICIES = {"hold", "use_last", "drop"}

_BASIC_REQUIRED = {"host", "port", "service_name", "user", "password"}
_WALLET_REQUIRED = {"dsn", "user", "password", "wallet_dir"}
# JDBC sidecar requires the same basic fields; wallet/TCPS not supported.
_JDBC_REQUIRED = {"host", "port", "service_name", "user", "password"}

_BRIDGE_REQUIRED = {
    "mqtt": {"host", "port", "qos", "topic_event", "topic_ack", "client_id"},
    "oracle": {
        "connect_timeout_seconds",
        "query_timeout_seconds",
        "pool_min",
        "pool_max",
        "instant_client_dir",
    },
    "oracle_jdbc": {"url", "connect_timeout_ms", "read_timeout_ms"},
    "network_watcher": {"poll_interval_seconds", "ssid_command"},
    "time_watcher": {"poll_interval_seconds", "sync_command"},
    "retry": {"initial_delay_seconds", "max_delay_seconds", "multiplier"},
    "circuit_breaker": {"permanent_ora_codes", "half_open_after_seconds"},
    "buffer": {"path", "max_rows"},
    "logging": {"level", "buffer_stats_interval_seconds"},
}
_DEVICE_REQUIRED = {"station": {"sta_no1", "sta_no2", "sta_no3"}}


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


def _validate_required(data: dict[str, Any], required: dict[str, set[str]], where: str) -> None:
    for section, keys in required.items():
        if section not in data:
            raise ConfigError(f"{where}: missing required section: {section}")
        if not isinstance(data[section], dict):
            raise ConfigError(f"{where}: section {section} must be a mapping")
        missing = keys - set(data[section].keys())
        if missing:
            raise ConfigError(f"{where}: missing required key(s) {sorted(missing)} in {section}")


def load_bridge_config(path: Path) -> dict[str, Any]:
    data = expand_env(load_yaml(path))
    _validate_required(data, _BRIDGE_REQUIRED, str(path))
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


def _validate_oracle_section(name: str, oracle: dict[str, Any]) -> None:
    cm = oracle.get("client_mode")
    if cm not in ALLOWED_CLIENT_MODES:
        raise ConfigError(
            f"profile {name}: client_mode must be one of "
            f"{sorted(ALLOWED_CLIENT_MODES)}, got {cm!r}"
        )
    am = oracle.get("auth_mode")
    if am not in ALLOWED_AUTH_MODES:
        raise ConfigError(
            f"profile {name}: auth_mode must be one of "
            f"{sorted(ALLOWED_AUTH_MODES)}, got {am!r}"
        )
    if cm == "jdbc":
        # The JDBC sidecar accepts only host/port/service_name + basic auth.
        # Old-verifier passwords (10G/0x939) work here because ojdbc11.jar
        # is the entire reason we have this client mode.
        if am != "basic":
            raise ConfigError(
                f"profile {name}: client_mode=jdbc requires auth_mode=basic "
                f"(wallet/TCPS unsupported by the sidecar), got {am!r}"
            )
        required = _JDBC_REQUIRED
    else:
        required = _BASIC_REQUIRED if am == "basic" else _WALLET_REQUIRED
    missing = required - set(oracle.keys())
    if missing:
        raise ConfigError(
            f"profile {name} ({cm}/{am}): missing required oracle key(s) {sorted(missing)}"
        )
    if "upcmpflg" in oracle:
        # Optional per-profile UPCMPFLG bind value. Must be a plain integer
        # (no string-as-int). YAML loaders coerce bare numbers to int already;
        # this catches operator typos like upcmpflg: "1" or upcmpflg: yes.
        val = oracle["upcmpflg"]
        if not isinstance(val, int) or isinstance(val, bool):
            raise ConfigError(
                f"profile {name}: oracle.upcmpflg must be an integer "
                f"(omit the key entirely to skip the UPCMPFLG column), got {val!r}"
            )


_STATION_KEYS = {"sta_no1", "sta_no2", "sta_no3"}


def _validate_optional_station(name: str, station: Any) -> None:
    if not isinstance(station, dict):
        raise ConfigError(f"profile {name}: 'station' override must be a mapping")
    missing = _STATION_KEYS - set(station.keys())
    if missing:
        raise ConfigError(
            f"profile {name}: station override is missing key(s) {sorted(missing)}"
        )


def _validate_optional_wifi(name: str, wifi: Any) -> None:
    """Validate the OPTIONAL Wi-Fi provisioning block on a profile.

    Consumed by operator tooling (verify_himereap_oracle.sh, future install
    helpers) -- the bridge service itself never reads this section. Bridge
    just observes the current SSID via nmcli; Wi-Fi setup is out-of-process.
    """
    if not isinstance(wifi, dict):
        raise ConfigError(f"profile {name}: 'wifi' must be a mapping")
    if "psk" not in wifi:
        raise ConfigError(f"profile {name}: wifi.psk is required when wifi section is present")
    if "static_ipv4" in wifi:
        s = wifi["static_ipv4"]
        if not isinstance(s, dict):
            raise ConfigError(f"profile {name}: wifi.static_ipv4 must be a mapping")
        for k in ("address", "gateway", "dns"):
            if k not in s:
                raise ConfigError(f"profile {name}: wifi.static_ipv4.{k} is required")
        if not isinstance(s["dns"], list):
            raise ConfigError(f"profile {name}: wifi.static_ipv4.dns must be a list")


def load_profiles_config(path: Path) -> dict[str, Any]:
    data = expand_env(load_yaml(path))
    if "profiles" not in data or not isinstance(data["profiles"], dict):
        raise ConfigError(f"{path}: top-level must contain a 'profiles' mapping")
    policy = data.get("unknown_ssid_policy", "hold")
    if policy not in ALLOWED_UNKNOWN_POLICIES:
        raise ConfigError(
            f"unknown_ssid_policy must be one of "
            f"{sorted(ALLOWED_UNKNOWN_POLICIES)}, got {policy!r}"
        )
    data["unknown_ssid_policy"] = policy
    for name, profile in data["profiles"].items():
        if "oracle" not in profile or not isinstance(profile["oracle"], dict):
            raise ConfigError(f"profile {name}: missing 'oracle' section")
        if "sntp" not in profile or not isinstance(profile["sntp"], dict):
            raise ConfigError(f"profile {name}: missing 'sntp' section")
        if "servers" not in profile["sntp"] or not isinstance(profile["sntp"]["servers"], list):
            raise ConfigError(f"profile {name}: sntp.servers must be a list")
        _validate_oracle_section(name, profile["oracle"])
        if "station" in profile:
            _validate_optional_station(name, profile["station"])
        if "wifi" in profile:
            _validate_optional_wifi(name, profile["wifi"])
    return data


def station_for_profile(profile: dict[str, Any], device_cfg: dict[str, Any]) -> dict[str, str]:
    """Return the (sta_no1, sta_no2, sta_no3) triple to use for this profile.

    Profile-level `station:` wins when present; otherwise fall back to the
    device-wide `device.yaml` station. Lets a single Pi report different
    station numbers depending on which factory Wi-Fi it is on.
    """
    if isinstance(profile.get("station"), dict):
        return profile["station"]
    return device_cfg["station"]


def needs_thick_mode(profiles: dict[str, Any]) -> bool:
    return any(p["oracle"].get("client_mode") == "thick" for p in profiles.values())


def list_all_sntp_servers(profiles: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for p in profiles.values():
        for s in p["sntp"]["servers"]:
            if s not in seen:
                seen.append(s)
    return seen
