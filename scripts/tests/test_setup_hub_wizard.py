"""対話ウィザード (setup-hub-wizard.sh) の検証。

ホスト名・固定IP・AP名の衝突は「動いているように見えて壊れる」。ウィザードが
親の origin.env と突き合わせて拒否することが、共存の防波堤になる。
"""
import os
import textwrap

from pathlib import Path

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

ORIGIN_PACK = textwrap.dedent("""\
    ORIGIN_HOSTNAME=raspberrypi5
    ORIGIN_FACTORY_IP=172.22.13.17/24
    ORIGIN_FACTORY_SSID=HIME-H-REAP
    ORIGIN_FACTORY_GW=172.22.13.1
    ORIGIN_FACTORY_DNS=10.166.1.70,10.166.1.17
    ORIGIN_AP_SSID=presence-hub
    ORIGIN_ORACLE_HOST=10.166.5.93
    ORIGIN_ORACLE_PORT=1521
    ORIGIN_ORACLE_SERVICE=HHC001
    ORIGIN_ORACLE_USER=ZHH001
    ORIGIN_ORACLE_TABLE=HF1RCM01
    ORIGIN_PARENT_STA_NO1=996
    ORIGIN_PARENT_STA_NO2=995
    ORIGIN_PARENT_STA_NO3=994
    """)

# ホスト名のあと。工場SSIDは Enter、固定IPは手入力、残りはコピー元。
_AFTER_SSID = [
    "172.22.13.21",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    "Abc12345",
    "ora12345",
]


def _kit_workdir(tmp_path: Path) -> Path:
    kit = tmp_path / ".kit"
    kit.mkdir()
    (kit / "origin.env").write_text(ORIGIN_PACK, encoding="utf-8")
    (tmp_path / "site.env.example").write_text(
        Path("site.env.example").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return tmp_path


def _run_hub_wizard(tmp_path: Path, lines: list[str]):
    return run_bash(
        "timeout 20 bash -c 'source scripts/setup-hub-wizard.sh; main'",
        env=_env({"WIZARD_WORKDIR": str(tmp_path), "WIZARD_DRY_RUN": "1"}),
        stdin="\n".join(lines) + "\n",
        check=False,
    )


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
    assert "ORIGIN_REPLACE=1" in out


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


def test_render_uses_wizard_factory_and_oracle_overrides(tmp_path):
    tmpl = tmp_path / "site.env.template"
    tmpl.write_text(TEMPLATE, encoding="utf-8")
    origin = tmp_path / "origin.env"
    origin.write_text(ORIGIN, encoding="utf-8")
    out = run_bash(
        f'{SITE}; {SOURCE}; '
        f'export WIZ_FACTORY_SSID=OTHER-SSID WIZ_FACTORY_GW=10.1.1.1 '
        f'WIZ_FACTORY_DNS=8.8.8.8 WIZ_ORACLE_HOST=10.0.0.9 '
        f'WIZ_ORACLE_PORT=1522 WIZ_ORACLE_SERVICE=OTHER '
        f'WIZ_ORACLE_USER=ZHH002 WIZ_ORACLE_TABLE=HF9; '
        f'wizard_render_site_env "{tmpl}" "{origin}" '
        f'presence-hub-2 172.22.13.18 sibling-hub',
        env=_env(),
    ).stdout
    assert "FACTORY_SSID=OTHER-SSID" in out
    assert "FACTORY_GW=10.1.1.1" in out
    assert "FACTORY_DNS=8.8.8.8" in out
    assert "ORACLE_HOST=10.0.0.9" in out
    assert "ORACLE_PORT=1522" in out
    assert "ORACLE_SERVICE=OTHER" in out
    assert "ORACLE_USER=ZHH002" in out
    assert "ORACLE_TABLE=HF9" in out


def test_factory_ip_matching_origin_is_allowed_when_replacing(tmp_path):
    origin = tmp_path / "origin.env"
    origin.write_text(ORIGIN, encoding="utf-8")
    proc = run_bash(
        f'{SITE}; {SOURCE}; wizard_validate_factory_ip 172.22.13.17 "{origin}" 1',
        env=_env(),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_ask_empty_reply_keeps_default():
    proc = run_bash(
        f'{SOURCE}; printf "\\n" | wizard_ask "工場の SSID" HIME-H-REAP',
        env=_env(),
    )
    assert proc.stdout.strip() == "HIME-H-REAP"


def test_ask_back_word_is_a_token():
    proc = run_bash(
        f'{SOURCE}; printf "戻る\\n" | wizard_ask "工場の SSID" HIME-H-REAP',
        env=_env(),
    )
    assert proc.stdout.strip() == "__WIZ_BACK__"


def test_factory_ip_rejects_out_of_range_octet(tmp_path):
    origin = tmp_path / "origin.env"
    origin.write_text(ORIGIN, encoding="utf-8")
    proc = run_bash(
        f'{SITE}; {SOURCE}; wizard_validate_factory_ip 999.1.1.1 "{origin}"',
        env=_env(),
        check=False,
    )
    assert proc.returncode != 0


def test_mode_two_text_does_not_promise_auto_join_without_psk():
    from pathlib import Path
    text = Path("scripts/setup-hub-wizard.sh").read_text(encoding="utf-8")
    assert "パスワードが旧ハブと同じときに限ります" in text
    assert "クローンした子が自動で付きます" not in text
    assert "間違えたら 0 で直前" in text
    assert "親機は本当に止まっていますか" in text


def test_coexist_prompts_keep_hostname_factory_ip_and_ap_ssid_separate():
    from pathlib import Path
    text = Path("scripts/setup-hub-wizard.sh").read_text(encoding="utf-8")
    assert 'wizard_ask "このハブのホスト名"' in text
    assert 'wizard_ask "このハブの工場固定IP"' in text
    assert 'wizard_ask "子Pi用ハブAPの Wi-Fi名"' in text
    # ホスト名そのものを AP 名にすると ① と ⑥ が同じ値に見える
    assert 'wizard_ask "ドングルの AP 名" "${hostname}"' not in text
    assert "wizard_default_ap_ssid" in text
    assert "wizard_sibling_name" in text


def test_sibling_name_appends_dash_two():
    proc = run_bash(
        f'{SOURCE}; wizard_sibling_name raspberrypi5 raspberrypi5-2',
        env=_env(),
    )
    assert proc.stdout.strip() == "raspberrypi5-2"
    proc = run_bash(
        f'{SOURCE}; wizard_sibling_name presence-hub presence-hub-2',
        env=_env(),
    )
    assert proc.stdout.strip() == "presence-hub-2"


def test_default_ap_ssid_is_hostname_dash_hub():
    proc = run_bash(
        f'{SOURCE}; wizard_default_ap_ssid tpc12345',
        env=_env(),
    )
    assert proc.stdout.strip() == "tpc12345-hub"
    proc = run_bash(
        f'{SOURCE}; wizard_default_ap_ssid raspberrypi5-2',
        env=_env(),
    )
    assert proc.stdout.strip() == "raspberrypi5-2-hub"
    proc = run_bash(
        f'{SOURCE}; wizard_default_ap_ssid "" presence-hub',
        env=_env(),
    )
    assert proc.stdout.strip() == "presence-hub"


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


def test_ask_zero_is_a_back_token():
    proc = run_bash(
        f'{SOURCE}; printf "0\\n" | wizard_ask "工場の SSID" HIME-H-REAP',
        env=_env(),
    )
    assert proc.stdout.strip() == "__WIZ_BACK__"


def test_ask_double_angle_is_a_back_token():
    proc = run_bash(
        f'{SOURCE}; printf "<<\\n" | wizard_ask "工場の SSID" HIME-H-REAP',
        env=_env(),
    )
    assert proc.stdout.strip() == "__WIZ_BACK__"


def test_hub_wizard_back_from_ssid_reasks_hostname(tmp_path):
    work = _kit_workdir(tmp_path)
    proc = _run_hub_wizard(
        work,
        ["1", "tpc-wrong", "0", "tpc99999", ""] + _AFTER_SSID + ["y"],
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "----- ① このハブのホスト名 -----" in proc.stdout
    assert proc.stdout.count("----- ① このハブのホスト名 -----") >= 2
    assert "① このハブのホスト名         : tpc99999" in proc.stdout
    assert "tpc-wrong" not in proc.stdout.split("----- 確認 -----")[-1]
    assert "DRY-RUN" in proc.stdout
    assert not (work / "site.env").exists()


def test_hub_wizard_confirm_jump_rewrites_factory_ip(tmp_path):
    work = _kit_workdir(tmp_path)
    proc = _run_hub_wizard(
        work,
        ["1", "tpc12345", ""]
        + _AFTER_SSID
        + ["3", "172.22.13.22"]
        + _AFTER_SSID[1:]
        + ["y"],
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    confirm = proc.stdout.split("----- 確認 -----")[-1]
    assert "172.22.13.22" in confirm
    assert "tpc12345" in confirm
    assert "DRY-RUN" in proc.stdout


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


def test_replace_env_key_keeps_other_lines(tmp_path):
    dest = tmp_path / "secrets.env"
    dest.write_text("ORACLE_PASSWORD_HHC=keep\nWIFI_AP_PSK=oldoldold\n", encoding="utf-8")
    run_bash(
        f'{SOURCE}; wizard_replace_env_key "{dest}" WIFI_AP_PSK newpsk99',
        env=_env(),
    )
    body = dest.read_text(encoding="utf-8")
    assert "ORACLE_PASSWORD_HHC=keep" in body
    assert "WIFI_AP_PSK=newpsk99" in body
    assert "oldoldold" not in body


def test_sync_psk_to_etc_reads_kit_file(tmp_path):
    kit = tmp_path / "ap-join.env"
    kit.write_text("AP_SSID=tpc12345-hub\nWIFI_AP_PSK=kitpsk99\n", encoding="utf-8")
    etc = tmp_path / "secrets.env"
    etc.write_text("ORACLE_PASSWORD_HHC=ora\nWIFI_AP_PSK=wrongpsk\n", encoding="utf-8")
    run_bash(
        f'{SOURCE}; wizard_sync_psk_to_etc "{kit}" "{etc}"',
        env=_env(),
    )
    body = etc.read_text(encoding="utf-8")
    assert "ORACLE_PASSWORD_HHC=ora" in body
    assert "WIFI_AP_PSK=kitpsk99" in body
    assert "wrongpsk" not in body


def test_already_configured_p_redoes_ap_psk_without_bootstrap(tmp_path):
    work = tmp_path
    kit = work / ".kit"
    kit.mkdir()
    (kit / "setup-complete").write_text("done\n", encoding="utf-8")
    (kit / "ap-join.env").write_text(
        "AP_SSID=tpc12345-hub\nWIFI_AP_PSK=wrongpsk\n", encoding="utf-8"
    )
    (kit / "secrets.env").write_text(
        "ORACLE_PASSWORD_HHC=ora\nWIFI_AP_PSK=wrongpsk\n", encoding="utf-8"
    )
    proc = run_bash(
        "timeout 20 bash -c 'source scripts/setup-hub-wizard.sh; main'",
        env=_env({"WIZARD_WORKDIR": str(work), "WIZARD_DRY_RUN": "1"}),
        stdin="p\nCorrect99\n\n",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "パスワードだけ" in proc.stdout or "パスワードを打ち間違えて" in proc.stdout
    join = (kit / "ap-join.env").read_text(encoding="utf-8")
    assert "WIFI_AP_PSK=Correct99" in join
    assert "AP_SSID=tpc12345-hub" in join
    secrets = (kit / "secrets.env").read_text(encoding="utf-8")
    assert "WIFI_AP_PSK=Correct99" in secrets
    assert "ORACLE_PASSWORD_HHC=ora" in secrets
    assert "DRY-RUN" in proc.stdout
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
