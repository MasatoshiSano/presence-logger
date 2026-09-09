"""フェーズ40(設定生成)を検証する。

profiles.yaml に station: を出さないのが要点。親の局番は device.yaml に
一本化する(設計書 4.3)。両方に書くと二重管理になり、片方だけ直して食い違う。
"""
import os
import textwrap

import yaml

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/bootstrap/40-configs.sh"

SITE = textwrap.dedent("""\
    HUB_HOSTNAME=presence-hub-2
    HUB_MODE=1
    FACTORY_SSID=HIME-H-REAP
    FACTORY_IP=172.22.13.18/24
    FACTORY_GW=172.22.13.1
    FACTORY_DNS=10.166.1.70,10.166.1.17
    FACTORY_SUBNETS="10.166.5.0/24"
    FACTORY_HIDDEN=yes
    SNTP_SERVERS="133.141.247.101"
    ORACLE_CLIENT_MODE=jdbc
    ORACLE_AUTH_MODE=basic
    ORACLE_HOST=10.166.5.93
    ORACLE_PORT=1521
    ORACLE_SERVICE=HHC001
    ORACLE_USER=ZHH001
    ORACLE_TABLE=HF1RCM01
    ORACLE_PASSWORD_VAR=ORACLE_PASSWORD_HHC
    UPCMPFLG=1
    UNKNOWN_SSID_POLICY=drop
    PARENT_STA_NO1=997
    PARENT_STA_NO2=996
    PARENT_STA_NO3=995
    AP_IF=wlan1
    AP_SSID=presence-hub
    AP_GW_IP=10.42.0.1
    AP_BAND=bg
    AP_CHANNEL=6
    HOME_SSID=UFI_103134
    ADMIN_SSID=F660P-sDcS-A
    """)


def _render(tmp_path, fn):
    env_file = tmp_path / "site.env"
    env_file.write_text(SITE, encoding="utf-8")
    return run_bash(
        f'{SOURCE}; site_env_load "{env_file}"; {fn}',
        env=dict(os.environ),
    ).stdout


def test_profiles_yaml_is_valid_yaml_keyed_by_ssid(tmp_path):
    doc = yaml.safe_load(_render(tmp_path, "configs_render_profiles_yaml"))
    assert "HIME-H-REAP" in doc["profiles"]


def test_profiles_yaml_carries_the_new_static_ip(tmp_path):
    doc = yaml.safe_load(_render(tmp_path, "configs_render_profiles_yaml"))
    p = doc["profiles"]["HIME-H-REAP"]
    assert p["wifi"]["static_ipv4"]["address"] == "172.22.13.18/24"
    assert p["wifi"]["static_ipv4"]["gateway"] == "172.22.13.1"
    assert p["wifi"]["static_ipv4"]["dns"] == ["10.166.1.70", "10.166.1.17"]


def test_profiles_yaml_has_no_station_override(tmp_path):
    # 親の局番は device.yaml に一本化する(設計書 4.3)
    doc = yaml.safe_load(_render(tmp_path, "configs_render_profiles_yaml"))
    assert "station" not in doc["profiles"]["HIME-H-REAP"]


def test_password_is_a_variable_reference_not_a_value(tmp_path):
    body = _render(tmp_path, "configs_render_profiles_yaml")
    assert "${ORACLE_PASSWORD_HHC}" in body


def test_unknown_ssid_policy_is_carried_through(tmp_path):
    doc = yaml.safe_load(_render(tmp_path, "configs_render_profiles_yaml"))
    assert doc["unknown_ssid_policy"] == "drop"


def test_device_yaml_uses_parent_sta_no(tmp_path):
    doc = yaml.safe_load(_render(tmp_path, "configs_render_device_yaml"))
    assert doc["device_id"] is None
    assert doc["station"] == {"sta_no1": "997", "sta_no2": "996", "sta_no3": "995"}


def test_missing_secret_keys_are_listed(tmp_path):
    secrets = tmp_path / "secrets.env"
    secrets.write_text("ORACLE_PASSWORD_HHC=x\n", encoding="utf-8")
    env_file = tmp_path / "site.env"
    env_file.write_text(SITE, encoding="utf-8")
    out = run_bash(
        f'{SOURCE}; site_env_load "{env_file}"; configs_missing_secret_keys "{secrets}"',
        env=dict(os.environ),
    ).stdout.split()
    assert "WIFI_PSK_HIMEREAP" in out
    assert "WIFI_AP_PSK" in out
    assert "ORACLE_PASSWORD_HHC" not in out
