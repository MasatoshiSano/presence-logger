"""WiFi切替(旧 ~/Desktop/WiFi切替)を検証する。

旧版は --away-from-f66 という現行機固有の前提を名前に埋めていた。新機では
保守用SSIDが別名になり得るので、SSID名は呼び出し側から与える。
"""
import os
import textwrap

from scripts.tests.shellhelp import run_bash

SOURCE = "source desktop/wifi-switch/switch-wifi.sh"

CONF = textwrap.dedent("""\
    # <NM接続名>       <ifname>  <表示名>       <警告>  <PSKキー名>
    F660P-sDcS-A       wlan0     F66            -       WIFI_PSK_F66
    GallaxyS23FE       wlan0     GallaxyS23FE   warn    WIFI_PSK_GALAXY
    presence-hub-ap    wlan1     presence-hub   -       -
    """)


def test_conf_is_parsed_into_records(tmp_path):
    f = tmp_path / "wifi-switch.conf"
    f.write_text(CONF, encoding="utf-8")
    out = run_bash(f'{SOURCE}; wifi_switch_parse_conf "{f}"',
                   env=dict(os.environ)).stdout.strip().splitlines()
    assert out[0] == "F660P-sDcS-A|wlan0|F66|-|WIFI_PSK_F66"
    assert out[2] == "presence-hub-ap|wlan1|presence-hub|-|-"
    assert len(out) == 3          # コメント行は落ちる


def test_switch_calls_nmcli_with_connection_and_interface(fake_bin):
    fake_bin("nmcli", 'printf "nmcli %s\\n" "$*" >> "$FAKE_LOG"; exit 0')
    run_bash(f'{SOURCE}; switch_wifi F660P-sDcS-A wlan0',
             env=dict(os.environ), check=False)
    log = fake_bin.log.read_text(encoding="utf-8")
    assert "connection up F660P-sDcS-A ifname wlan0" in log


def test_warning_names_the_admin_ssid():
    out = run_bash(f'{SOURCE}; warn_disconnect_message F660P-sDcS-A',
                   env=dict(os.environ)).stdout
    assert "F660P-sDcS-A" in out
    assert "away-from-f66" not in out        # 現行機固有の名前を残さない


def test_ap_row_carries_a_dash_for_its_psk_key(tmp_path):
    # presence-hub-ap はフェーズ50 が作る。播種側(Task 11)はこの "-" を見て
    # 二重作成を避けるため、パース結果に残っていることが前提になる
    f = tmp_path / "wifi-switch.conf"
    f.write_text(CONF, encoding="utf-8")
    out = run_bash(f'{SOURCE}; wifi_switch_parse_conf "{f}"',
                   env=dict(os.environ)).stdout.strip().splitlines()
    ap = [r for r in out if r.startswith("presence-hub-ap")][0]
    assert ap.endswith("|-")


def test_warning_uses_the_ssid_argument():
    out = run_bash(f'{SOURCE}; warn_disconnect_message some-other-admin',
                   env=dict(os.environ)).stdout
    assert "some-other-admin" in out
    assert "F660P-sDcS-A" not in out
    assert "away-from-f66" not in out


def test_switch_returns_nonzero_when_nmcli_fails(fake_bin):
    fake_bin("nmcli", "exit 1")
    proc = run_bash(f'{SOURCE}; switch_wifi F660P-sDcS-A wlan0',
                    env=dict(os.environ), check=False)
    assert proc.returncode == 1


def test_launcher_template_has_placeholders():
    from scripts.tests.shellhelp import REPO_ROOT
    body = (REPO_ROOT / "desktop/wifi-switch/launcher.desktop.tmpl").read_text(
        encoding="utf-8")
    for token in ("__LABEL__", "__IFNAME__", "__CONN__", "__WARN_ARG__",
                  "__TOOLS_DIR__", "__COMMENT__"):
        assert token in body
