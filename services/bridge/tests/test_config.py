from pathlib import Path

import pytest

from services.bridge.src.config import (
    ConfigError,
    expand_env,
    list_all_sntp_servers,
    load_profiles_config,
    load_yaml,
    needs_thick_mode,
    station_for_profile,
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


def test_load_profiles_jdbc_mode_basic_auth_accepted(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ORACLE_PASSWORD_ONPREM", "ZHH001_99")
    p = tmp_path / "profiles.yaml"
    p.write_text("""
profiles:
  taden-ot-ap:
    description: Factory internal Wi-Fi (HHS001 via JDBC sidecar)
    sntp: {servers: [192.168.250.1]}
    oracle:
      client_mode: jdbc
      auth_mode: basic
      host: 10.168.252.16
      port: 1521
      service_name: HHS001
      user: ZHH001
      password: ${ORACLE_PASSWORD_ONPREM}
      table_name: HF1RCM01
unknown_ssid_policy: hold
""")
    cfg = load_profiles_config(p)
    assert cfg["profiles"]["taden-ot-ap"]["oracle"]["client_mode"] == "jdbc"
    assert cfg["profiles"]["taden-ot-ap"]["oracle"]["password"] == "ZHH001_99"


def test_load_profiles_jdbc_mode_rejects_wallet_auth(tmp_path: Path):
    p = tmp_path / "profiles.yaml"
    p.write_text("""
profiles:
  x:
    description: X
    sntp: {servers: [ntp.x]}
    oracle:
      client_mode: jdbc
      auth_mode: wallet
      dsn: foo
      user: u
      password: p
      wallet_dir: /w
      table_name: HF1RCM01
unknown_ssid_policy: hold
""")
    with pytest.raises(ConfigError, match="jdbc.*basic"):
        load_profiles_config(p)


def test_load_profiles_jdbc_mode_requires_service_name(tmp_path: Path):
    p = tmp_path / "profiles.yaml"
    p.write_text("""
profiles:
  x:
    description: X
    sntp: {servers: [ntp.x]}
    oracle:
      client_mode: jdbc
      auth_mode: basic
      host: h
      port: 1521
      user: u
      password: p
      table_name: HF1RCM01
unknown_ssid_policy: hold
""")
    with pytest.raises(ConfigError, match="service_name"):
        load_profiles_config(p)


def test_needs_thick_mode_false_when_jdbc_present():
    """JDBC profiles must NOT trigger python-oracledb thick-mode init."""
    profiles = {
        "x": {"oracle": {"client_mode": "jdbc", "auth_mode": "basic"}},
    }
    assert needs_thick_mode(profiles) is False


def test_load_profiles_accepts_optional_wifi_and_station(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ORACLE_PASSWORD_ONPREM", "ZHH001_99")
    monkeypatch.setenv("WIFI_PSK_TADEN", "Cisco@12345")
    p = tmp_path / "profiles.yaml"
    p.write_text("""
profiles:
  taden-ot-ap:
    description: factory
    wifi:
      psk: ${WIFI_PSK_TADEN}
      hidden: true
      static_ipv4:
        address: 172.29.1.4/24
        gateway: 172.29.1.254
        dns: [192.168.250.1]
    station:
      sta_no1: "996"
      sta_no2: "995"
      sta_no3: "994"
    sntp: {servers: [192.168.250.1]}
    oracle:
      client_mode: jdbc
      auth_mode: basic
      host: 10.168.252.16
      port: 1521
      service_name: HHS001
      user: ZHH001
      password: ${ORACLE_PASSWORD_ONPREM}
      table_name: HF1RCM01
unknown_ssid_policy: hold
""")
    cfg = load_profiles_config(p)
    prof = cfg["profiles"]["taden-ot-ap"]
    assert prof["wifi"]["psk"] == "Cisco@12345"
    assert prof["wifi"]["static_ipv4"]["address"] == "172.29.1.4/24"
    assert prof["wifi"]["static_ipv4"]["dns"] == ["192.168.250.1"]
    assert prof["station"] == {"sta_no1": "996", "sta_no2": "995", "sta_no3": "994"}


def test_load_profiles_wifi_requires_psk(tmp_path: Path):
    p = tmp_path / "profiles.yaml"
    p.write_text("""
profiles:
  s:
    description: x
    wifi:
      hidden: true
    sntp: {servers: [n]}
    oracle:
      client_mode: jdbc
      auth_mode: basic
      host: h
      port: 1521
      service_name: s
      user: u
      password: p
      table_name: HF1RCM01
unknown_ssid_policy: hold
""")
    with pytest.raises(ConfigError, match="wifi.psk"):
        load_profiles_config(p)


def test_load_profiles_partial_station_rejected(tmp_path: Path):
    p = tmp_path / "profiles.yaml"
    p.write_text("""
profiles:
  s:
    description: x
    station:
      sta_no1: "1"
      sta_no2: "2"
    sntp: {servers: [n]}
    oracle:
      client_mode: jdbc
      auth_mode: basic
      host: h
      port: 1521
      service_name: s
      user: u
      password: p
      table_name: HF1RCM01
unknown_ssid_policy: hold
""")
    with pytest.raises(ConfigError, match="sta_no3"):
        load_profiles_config(p)


def test_station_for_profile_prefers_profile_override():
    profile = {"station": {"sta_no1": "996", "sta_no2": "995", "sta_no3": "994"}}
    device = {"station": {"sta_no1": "001", "sta_no2": "002", "sta_no3": "003"}}
    assert station_for_profile(profile, device) == \
        {"sta_no1": "996", "sta_no2": "995", "sta_no3": "994"}


def test_load_profiles_accepts_optional_upcmpflg_integer(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ORACLE_PASSWORD_ONPREM", "ZHH001_99")
    p = tmp_path / "profiles.yaml"
    p.write_text("""
profiles:
  taden-ot-ap:
    description: x
    sntp: {servers: [n]}
    oracle:
      client_mode: jdbc
      auth_mode: basic
      host: 10.168.252.16
      port: 1521
      service_name: HHS001
      user: ZHH001
      password: ${ORACLE_PASSWORD_ONPREM}
      table_name: HF1RCM01
      upcmpflg: 1
unknown_ssid_policy: hold
""")
    cfg = load_profiles_config(p)
    assert cfg["profiles"]["taden-ot-ap"]["oracle"]["upcmpflg"] == 1


def test_load_profiles_rejects_string_upcmpflg(tmp_path: Path):
    p = tmp_path / "profiles.yaml"
    p.write_text("""
profiles:
  s:
    description: x
    sntp: {servers: [n]}
    oracle:
      client_mode: jdbc
      auth_mode: basic
      host: h
      port: 1521
      service_name: S
      user: u
      password: p
      table_name: HF1RCM01
      upcmpflg: "1"
unknown_ssid_policy: hold
""")
    with pytest.raises(ConfigError, match="upcmpflg.*integer"):
        load_profiles_config(p)


def test_load_profiles_rejects_bool_upcmpflg(tmp_path: Path):
    """Common typo: YAML interprets 'yes' as True. Reject explicitly."""
    p = tmp_path / "profiles.yaml"
    p.write_text("""
profiles:
  s:
    description: x
    sntp: {servers: [n]}
    oracle:
      client_mode: jdbc
      auth_mode: basic
      host: h
      port: 1521
      service_name: S
      user: u
      password: p
      table_name: HF1RCM01
      upcmpflg: yes
unknown_ssid_policy: hold
""")
    with pytest.raises(ConfigError, match="upcmpflg.*integer"):
        load_profiles_config(p)


def test_station_for_profile_falls_back_to_device():
    profile = {"description": "no station override"}
    device = {"station": {"sta_no1": "001", "sta_no2": "002", "sta_no3": "003"}}
    assert station_for_profile(profile, device) == \
        {"sta_no1": "001", "sta_no2": "002", "sta_no3": "003"}


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
