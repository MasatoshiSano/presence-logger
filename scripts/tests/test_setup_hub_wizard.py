"""対話ウィザード (setup-hub-wizard.sh) の検証。

ホスト名・固定IP・AP名の衝突は「動いているように見えて壊れる」。ウィザードが
親の origin.env と突き合わせて拒否することが、共存の防波堤になる。
"""
import os
import textwrap

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/setup-hub-wizard.sh"
SITE = "source scripts/lib/site-env.sh"


def _env(extra=None):
    env = dict(os.environ)
    if extra:
        env.update(extra)
    return env


TEMPLATE = textwrap.dedent("""\
    HUB_HOSTNAME=
    HUB_MODE=1
    FACTORY_SSID=HIME-H-REAP
    FACTORY_IP=172.22.13.18/24
    FACTORY_GW=172.22.13.1
    FACTORY_DNS=10.166.1.70,10.166.1.17
    FACTORY_SUBNETS="10.166.5.0/24"
    SNTP_SERVERS="133.141.247.101"
    ORACLE_CLIENT_MODE=jdbc
    ORACLE_AUTH_MODE=basic
    ORACLE_HOST=10.166.5.93
    ORACLE_PORT=1521
    ORACLE_SERVICE=HHC001
    ORACLE_USER=ZHH001
    ORACLE_TABLE=HF1RCM01
    ORACLE_PASSWORD_VAR=ORACLE_PASSWORD_HHC
    PARENT_STA_NO1=996
    PARENT_STA_NO2=995
    PARENT_STA_NO3=994
    AP_IF=wlan1
    AP_SSID=
    AP_GW_IP=10.42.0.1
    AP_BAND=bg
    AP_CHANNEL=6
    HOME_SSID=UFI_103134
    ADMIN_SSID=F660P-sDcS-A
    """)

ORIGIN = textwrap.dedent("""\
    ORIGIN_HOSTNAME=raspberrypi5
    ORIGIN_FACTORY_IP=172.22.13.17/24
    ORIGIN_AP_SSID=presence-hub
    ORIGIN_PARENT_STA_NO1=996
    ORIGIN_PARENT_STA_NO2=995
    ORIGIN_PARENT_STA_NO3=994
    """)


def test_hostname_matching_origin_is_rejected(tmp_path):
    origin = tmp_path / "origin.env"
    origin.write_text(ORIGIN, encoding="utf-8")
    proc = run_bash(
        f'{SITE}; {SOURCE}; wizard_validate_hostname raspberrypi5 "{origin}"',
        env=_env(),
        check=False,
    )
    assert proc.returncode != 0
    assert "raspberrypi5" in proc.stderr


def test_hostname_with_hyphen_is_accepted_when_unique(tmp_path):
    origin = tmp_path / "origin.env"
    origin.write_text(ORIGIN, encoding="utf-8")
    proc = run_bash(
        f'{SITE}; {SOURCE}; wizard_validate_hostname presence-hub-2 "{origin}"',
        env=_env(),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_factory_ip_matching_origin_is_rejected(tmp_path):
    origin = tmp_path / "origin.env"
    origin.write_text(ORIGIN, encoding="utf-8")
    proc = run_bash(
        f'{SITE}; {SOURCE}; wizard_validate_factory_ip 172.22.13.17 "{origin}"',
        env=_env(),
        check=False,
    )
    assert proc.returncode != 0
    assert "172.22.13.17" in proc.stderr


def test_ap_ssid_matching_origin_is_rejected(tmp_path):
    origin = tmp_path / "origin.env"
    origin.write_text(ORIGIN, encoding="utf-8")
    proc = run_bash(
        f'{SITE}; {SOURCE}; wizard_validate_ap_ssid presence-hub "{origin}"',
        env=_env(),
        check=False,
    )
    assert proc.returncode != 0
    assert "presence-hub" in proc.stderr


def test_ap_ssid_matching_origin_is_allowed_when_replacing(tmp_path):
    origin = tmp_path / "origin.env"
    origin.write_text(ORIGIN, encoding="utf-8")
    proc = run_bash(
        f'{SITE}; {SOURCE}; wizard_validate_ap_ssid presence-hub "{origin}" 1',
        env=_env(),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_render_same_ap_ssid_writes_allow_flag(tmp_path):
    tmpl = tmp_path / "site.env.template"
    tmpl.write_text(TEMPLATE, encoding="utf-8")
    origin = tmp_path / "origin.env"
    origin.write_text(ORIGIN, encoding="utf-8")
    out = run_bash(
        f'{SITE}; {SOURCE}; wizard_render_site_env "{tmpl}" "{origin}" '
        f'presence-hub-2 172.22.13.18 presence-hub 1',
        env=_env(),
    ).stdout
    assert "AP_SSID=presence-hub" in out
    assert "ORIGIN_ALLOW_SAME_AP=1" in out


def test_short_ap_password_is_rejected():
    proc = run_bash(
        f'{SOURCE}; wizard_validate_ap_psk short',
        env=_env(),
        check=False,
    )
    assert proc.returncode != 0


def test_eight_char_ap_password_is_accepted():
    proc = run_bash(
        f'{SOURCE}; wizard_validate_ap_psk 12345678',
        env=_env(),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_render_inherits_oracle_and_factory_ssid_and_shifts_sta_no(tmp_path):
    tmpl = tmp_path / "site.env.template"
    tmpl.write_text(TEMPLATE, encoding="utf-8")
    origin = tmp_path / "origin.env"
    origin.write_text(ORIGIN, encoding="utf-8")
    out = tmp_path / "site.env"
    proc = run_bash(
        f'{SITE}; {SOURCE}; wizard_render_site_env "{tmpl}" "{origin}" '
        f'presence-hub-2 172.22.13.18/24 sibling-hub > "{out}"',
        env=_env(),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    body = out.read_text(encoding="utf-8")
    assert "HUB_HOSTNAME=presence-hub-2" in body
    assert "HUB_MODE=1" in body
    assert "FACTORY_SSID=HIME-H-REAP" in body
    assert "FACTORY_IP=172.22.13.18/24" in body
    assert "ORACLE_HOST=10.166.5.93" in body
    assert "AP_SSID=sibling-hub" in body
    assert "AP_GW_IP=10.42.0.1" in body
    # 親 996/995/994 から +3。996 をそのまま使うと衝突する
    assert "PARENT_STA_NO1=999" in body
    assert "PARENT_STA_NO2=998" in body
    assert "PARENT_STA_NO3=997" in body


def test_render_appends_prefix_when_user_omits_it(tmp_path):
    tmpl = tmp_path / "site.env.template"
    tmpl.write_text(TEMPLATE, encoding="utf-8")
    origin = tmp_path / "origin.env"
    origin.write_text(ORIGIN, encoding="utf-8")
    out = run_bash(
        f'{SITE}; {SOURCE}; wizard_render_site_env "{tmpl}" "{origin}" '
        f'presence-hub-2 172.22.13.18 sibling-hub',
        env=_env(),
    ).stdout
    assert "FACTORY_IP=172.22.13.18/24" in out


def test_merge_secrets_sets_typed_keys_and_keeps_factory_psk(tmp_path):
    tmpl = tmp_path / "secrets.env.template"
    tmpl.write_text("WIFI_PSK_HIMEREAP=factory\nWIFI_PSK_HOME=home\n", encoding="utf-8")
    out = tmp_path / "secrets.env"
    run_bash(
        f'{SOURCE}; wizard_merge_secrets "{tmpl}" "{out}" ORACLE_PASSWORD_HHC '
        f'oracle-pass ap-secret9',
        env=_env(),
    )
    body = out.read_text(encoding="utf-8")
    assert "ORACLE_PASSWORD_HHC=oracle-pass" in body
    assert "WIFI_AP_PSK=ap-secret9" in body
    assert "WIFI_PSK_HIMEREAP=factory" in body
    assert "WIFI_PSK_HOME=home" in body


def test_write_ap_join_env_from_wizard_helpers(tmp_path):
    dest = tmp_path / ".kit" / "ap-join.env"
    dest.parent.mkdir()
    run_bash(
        f'{SITE}; {SOURCE}; write_ap_join_env "{dest}" sibling-hub ap-secret9',
        env=_env(),
    )
    body = dest.read_text(encoding="utf-8")
    assert "AP_SSID=sibling-hub" in body
    assert "WIFI_AP_PSK=ap-secret9" in body


def test_already_configured_when_marker_exists(tmp_path):
    marker = tmp_path / "setup-complete"
    marker.write_text("ok\n", encoding="utf-8")
    proc = run_bash(
        f'{SOURCE}; wizard_already_configured "{marker}"',
        env=_env(),
        check=False,
    )
    assert proc.returncode == 0
    proc2 = run_bash(
        f'{SOURCE}; wizard_already_configured "{tmp_path}/missing"',
        env=_env(),
        check=False,
    )
    assert proc2.returncode != 0
