"""フェーズ20 ホスト名とフェーズ40 設定生成。"""
import os

from scripts.tests.shellhelp import run_bash

BASE = "source scripts/bootstrap/20-base-packages.sh"
CFG = "source scripts/bootstrap/40-configs.sh"


def test_base_packages_include_compose_plugin():
    out = run_bash(f"{BASE}; base_packages", env=dict(os.environ)).stdout.split()
    assert "docker.io" in out
    assert "docker-compose-plugin" in out
    assert "python3-yaml" in out


def test_hostname_rewrite_does_not_break_hyphenated_child_names(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1\tlocalhost\n127.0.1.1\traspberrypi\n10.0.0.2\tpizero2w-2\n", encoding="utf-8")
    hn = tmp_path / "hostname"
    hn.write_text("raspberrypi\n", encoding="utf-8")
    run_bash(
        f'{BASE}; base_set_hostname presence-hub-2 "{hn}" "{hosts}"',
        env=dict(os.environ),
    )
    body = hosts.read_text(encoding="utf-8")
    assert "presence-hub-2" in body
    assert "pizero2w-2" in body
    assert hn.read_text(encoding="utf-8").strip() == "presence-hub-2"


def test_profiles_yaml_has_no_station_block():
    out = run_bash(
        "source scripts/lib/site-env.sh; "
        + CFG
        + "; "
        + "FACTORY_SSID=HIME-H-REAP FACTORY_IP=172.22.13.18/24 FACTORY_GW=172.22.13.1 "
        "FACTORY_DNS=10.166.1.70,10.166.1.17 FACTORY_HIDDEN=yes "
        "SNTP_SERVERS=133.141.247.101 ORACLE_CLIENT_MODE=jdbc ORACLE_AUTH_MODE=basic "
        "ORACLE_HOST=10.166.5.93 ORACLE_PORT=1521 ORACLE_SERVICE=HHC001 "
        "ORACLE_USER=ZHH001 ORACLE_TABLE=HF1RCM01 ORACLE_PASSWORD_VAR=ORACLE_PASSWORD_HHC "
        "UPCMPFLG=1 UNKNOWN_SSID_POLICY=drop configs_render_profiles",
        env=dict(os.environ),
    ).stdout
    assert "station:" not in out
    assert "HIME-H-REAP" in out
    assert "172.22.13.18/24" in out
    assert "${ORACLE_PASSWORD_HHC}" in out
    assert "${WIFI_PSK_HIMEREAP}" in out
