"""フェーズ70(デスクトップ配置)を検証する。

既存ランチャーは /home/pi/Desktop/... を直書きしており、ユーザー名が pi 以外
だと壊れる。テンプレートから生成する。
"""
import os

from scripts.tests.shellhelp import REPO_ROOT, run_bash

SOURCE = "source scripts/bootstrap/70-desktop.sh"


def test_launcher_has_no_hardcoded_home():
    for p in (REPO_ROOT / "desktop" / "launchers").glob("*.desktop"):
        body = p.read_text(encoding="utf-8")
        assert "/home/pi/" not in body, f"{p.name} に /home/pi/ が残っている"


def test_rendered_launcher_points_at_the_given_tools_dir(tmp_path):
    tmpl = REPO_ROOT / "desktop" / "wifi-switch" / "launcher.desktop.tmpl"
    out = run_bash(
        f'{SOURCE}; desktop_render_launcher "{tmpl}" F660P-sDcS-A wlan0 F66 - '
        f'"/home/alice/Desktop/presence-tools" F660P-sDcS-A',
        env=dict(os.environ)).stdout
    assert "/home/alice/Desktop/presence-tools/switch-wifi.sh" in out
    assert "__TOOLS_DIR__" not in out


def test_warn_row_gets_the_warn_argument(tmp_path):
    tmpl = REPO_ROOT / "desktop" / "wifi-switch" / "launcher.desktop.tmpl"
    out = run_bash(
        f'{SOURCE}; desktop_render_launcher "{tmpl}" GallaxyS23FE wlan0 GallaxyS23FE warn '
        f'"/tmp/tools" F660P-sDcS-A',
        env=dict(os.environ)).stdout
    assert '--warn-disconnect "F660P-sDcS-A"' in out


def test_existing_profile_is_not_recreated(tmp_path, fake_bin):
    fake_bin("nmcli", '''
if [ "$1" = "-t" ]; then echo "F660P-sDcS-A"; exit 0; fi
printf "nmcli %s\\n" "$*" >> "$FAKE_LOG"
''')
    secrets = tmp_path / "secrets.env"
    secrets.write_text("WIFI_PSK_F66=hunter2\n", encoding="utf-8")
    run_bash(
        f'{SOURCE}; desktop_seed_profile F660P-sDcS-A wlan0 WIFI_PSK_F66 "{secrets}"',
        env=dict(os.environ), check=False)
    assert "connection add" not in fake_bin.log.read_text(encoding="utf-8")


def test_missing_psk_key_is_reported_not_silently_skipped(tmp_path, fake_bin):
    fake_bin("nmcli", 'if [ "$1" = "-t" ]; then exit 0; fi; exit 0')
    secrets = tmp_path / "secrets.env"
    secrets.write_text("", encoding="utf-8")
    proc = run_bash(
        f'{SOURCE}; desktop_seed_profile F660P-sDcS-A wlan0 WIFI_PSK_F66 "{secrets}"',
        env=dict(os.environ), check=False)
    assert proc.returncode != 0
    assert "WIFI_PSK_F66" in proc.stderr


def test_non_warn_row_has_no_warn_disconnect():
    tmpl = REPO_ROOT / "desktop" / "wifi-switch" / "launcher.desktop.tmpl"
    out = run_bash(
        f'{SOURCE}; desktop_render_launcher "{tmpl}" F660P-sDcS-A wlan0 F66 - '
        f'"/tmp/tools" F660P-sDcS-A',
        env=dict(os.environ)).stdout
    assert "--warn-disconnect" not in out


def test_warn_argument_uses_the_given_admin_ssid():
    tmpl = REPO_ROOT / "desktop" / "wifi-switch" / "launcher.desktop.tmpl"
    out = run_bash(
        f'{SOURCE}; desktop_render_launcher "{tmpl}" GallaxyS23FE wlan0 GallaxyS23FE warn '
        f'"/tmp/tools" some-other-admin',
        env=dict(os.environ)).stdout
    assert '--warn-disconnect "some-other-admin"' in out
    assert "F660P-sDcS-A" not in out


def test_similar_connection_name_is_not_treated_as_existing(tmp_path, fake_bin):
    fake_bin("nmcli", '''
if [ "$1" = "-t" ]; then echo "F660P-sDcS-A-guest"; exit 0; fi
printf "nmcli %s\\n" "$*" >> "$FAKE_LOG"
''')
    secrets = tmp_path / "secrets.env"
    secrets.write_text("WIFI_PSK_F66=hunter2\n", encoding="utf-8")
    run_bash(
        f'{SOURCE}; desktop_seed_profile F660P-sDcS-A wlan0 WIFI_PSK_F66 "{secrets}"',
        env=dict(os.environ), check=False)
    assert "connection add" in fake_bin.log.read_text(encoding="utf-8")


def test_dash_psk_key_does_not_seed(tmp_path, fake_bin):
    fake_bin("nmcli", 'printf "nmcli %s\\n" "$*" >> "$FAKE_LOG"')
    secrets = tmp_path / "secrets.env"
    secrets.write_text("WIFI_AP_PSK=hunter2\n", encoding="utf-8")
    run_bash(
        f'{SOURCE}; desktop_seed_profile presence-hub-ap wlan1 - "{secrets}"',
        env=dict(os.environ), check=False)
    assert "connection add" not in fake_bin.log.read_text(encoding="utf-8")
