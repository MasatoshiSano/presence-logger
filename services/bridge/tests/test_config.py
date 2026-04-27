from pathlib import Path

import pytest

from services.bridge.src.config import (
    ConfigError,
    expand_env,
    list_all_sntp_servers,
    load_profiles_config,
    load_yaml,
    needs_thick_mode,
)


def test_load_yaml_basic(tmp_path: Path):
    p = tmp_path / "x.yaml"
    p.write_text("a: 1\n")
    assert load_yaml(p) == {"a": 1}


def test_expand_env_basic(monkeypatch):
    monkeypatch.setenv("FOO", "bar")
    assert expand_env({"x": "${FOO}"}) == {"x": "bar"}


def test_load_profiles_basic_mode_valid(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ORACLE_PASSWORD_A", "p1")
    p = tmp_path / "profiles.yaml"
    p.write_text("""
profiles:
  factory_a_wifi:
    description: A
    sntp: {servers: [ntp.a]}
    oracle:
      client_mode: thin
      auth_mode: basic
      host: 10.0.0.1
      port: 1521
      service_name: PRDDB
      user: u
      password: ${ORACLE_PASSWORD_A}
      table_name: HF1RCM01
unknown_ssid_policy: hold
""")
    cfg = load_profiles_config(p)
    assert "factory_a_wifi" in cfg["profiles"]
    assert cfg["profiles"]["factory_a_wifi"]["oracle"]["password"] == "p1"
    assert cfg["unknown_ssid_policy"] == "hold"


def test_load_profiles_wallet_mode_requires_wallet_dir(tmp_path: Path):
    p = tmp_path / "profiles.yaml"
    p.write_text("""
profiles:
  factory_b_wifi:
    description: B
    sntp: {servers: [ntp.b]}
    oracle:
      client_mode: thin
      auth_mode: wallet
      dsn: myadb_high
      user: u
      password: pass
      table_name: HF1RCM01
unknown_ssid_policy: hold
""")
    with pytest.raises(ConfigError, match="wallet_dir"):
        load_profiles_config(p)


def test_load_profiles_basic_mode_requires_host(tmp_path: Path):
    p = tmp_path / "profiles.yaml"
    p.write_text("""
profiles:
  factory_a_wifi:
    description: A
    sntp: {servers: [ntp.a]}
    oracle:
      client_mode: thin
      auth_mode: basic
      port: 1521
      service_name: PRDDB
      user: u
      password: p
      table_name: HF1RCM01
unknown_ssid_policy: hold
""")
    with pytest.raises(ConfigError, match="host"):
        load_profiles_config(p)


def test_load_profiles_invalid_client_mode_rejected(tmp_path: Path):
    p = tmp_path / "profiles.yaml"
    p.write_text("""
profiles:
  x:
    description: X
    sntp: {servers: [ntp.x]}
    oracle:
      client_mode: heavy
      auth_mode: basic
      host: h
      port: 1521
      service_name: s
      user: u
      password: p
      table_name: HF1RCM01
unknown_ssid_policy: hold
""")
    with pytest.raises(ConfigError, match="client_mode"):
        load_profiles_config(p)


def test_load_profiles_invalid_unknown_ssid_policy(tmp_path: Path):
    p = tmp_path / "profiles.yaml"
    p.write_text("""
profiles: {}
unknown_ssid_policy: nope
""")
    with pytest.raises(ConfigError, match="unknown_ssid_policy"):
        load_profiles_config(p)


def test_needs_thick_mode_true_when_any_profile_thick():
    profiles = {
        "a": {"oracle": {"client_mode": "thin", "auth_mode": "basic"}},
        "b": {"oracle": {"client_mode": "thick", "auth_mode": "basic"}},
    }
    assert needs_thick_mode(profiles) is True


def test_needs_thick_mode_false_when_all_thin():
    profiles = {
        "a": {"oracle": {"client_mode": "thin", "auth_mode": "basic"}},
        "b": {"oracle": {"client_mode": "thin", "auth_mode": "wallet"}},
    }
    assert needs_thick_mode(profiles) is False


def test_list_all_sntp_servers_dedups_and_orders():
    profiles = {
        "a": {"sntp": {"servers": ["ntp.a", "ntp.shared"]}},
        "b": {"sntp": {"servers": ["ntp.b", "ntp.shared"]}},
    }
    out = list_all_sntp_servers(profiles)
    assert "ntp.a" in out
    assert "ntp.b" in out
    assert "ntp.shared" in out
    # No duplicates
    assert len(out) == len(set(out))
