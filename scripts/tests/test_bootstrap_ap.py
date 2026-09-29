"""フェーズ50(子AP)を検証する。

同一SSIDのAPが2つ生きると子がどちらに繋ぐか不定になり、DEPLOY.md に記録の
ある相互切断事故と同じ構図になる。AP を上げる *前* に止めるのが要点。
"""
import os
import textwrap

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/bootstrap/50-ap.sh"

SITE = textwrap.dedent("""\
    AP_IF=wlan1
    AP_SSID=presence-hub
    AP_GW_IP=10.42.0.1
    AP_BAND=bg
    AP_CHANNEL=6
    HOME_SSID=UFI_103134
    """)


def _site(tmp_path, body=SITE):
    f = tmp_path / "site.env"
    f.write_text(body, encoding="utf-8")
    return f


def test_duplicate_ssid_is_detected(tmp_path, fake_bin):
    fake_bin("nmcli", 'echo "presence-hub:70:WPA2"')
    f = _site(tmp_path)
    proc = run_bash(f'{SOURCE}; site_env_load "{f}"; ap_duplicate_ssid_present presence-hub',
                    env=dict(os.environ), check=False)
    assert proc.returncode == 0


def test_no_duplicate_when_absent(tmp_path, fake_bin):
    fake_bin("nmcli", 'echo "some-other-ap:70:WPA2"')
    f = _site(tmp_path)
    proc = run_bash(f'{SOURCE}; site_env_load "{f}"; ap_duplicate_ssid_present presence-hub',
                    env=dict(os.environ), check=False)
    assert proc.returncode != 0


def test_duplicate_ssid_is_exact_match(tmp_path, fake_bin):
    fake_bin("nmcli", 'echo "presence-hub-guest:70:WPA2"')
    f = _site(tmp_path)
    proc = run_bash(f'{SOURCE}; site_env_load "{f}"; ap_duplicate_ssid_present presence-hub',
                    env=dict(os.environ), check=False)
    assert proc.returncode != 0


def test_default_gateway_needs_no_explicit_address(tmp_path):
    f = _site(tmp_path)
    proc = run_bash(f'{SOURCE}; site_env_load "{f}"; ap_needs_explicit_address',
                    env=dict(os.environ), check=False)
    assert proc.returncode != 0


def test_non_default_gateway_needs_explicit_address(tmp_path):
    # ipv4.method shared は 10.42.0.1/24 を自動で付ける。他の値にするには
    # ipv4.addresses の明示指定が要る
    f = _site(tmp_path, SITE.replace("AP_GW_IP=10.42.0.1", "AP_GW_IP=10.43.0.1"))
    proc = run_bash(f'{SOURCE}; site_env_load "{f}"; ap_needs_explicit_address',
                    env=dict(os.environ), check=False)
    assert proc.returncode == 0


def test_env_args_forward_site_values(tmp_path):
    f = _site(tmp_path)
    out = run_bash(f'{SOURCE}; site_env_load "{f}"; ap_env_args',
                   env=dict(os.environ)).stdout
    assert "AP_IF=wlan1" in out
    assert "AP_SSID=presence-hub" in out
    assert "AP_CHANNEL=6" in out
    assert "UFI_CONN=UFI_103134" not in out
    assert "AP_CONN=presence-hub-ap" in out
    assert "AP_BAND=bg" in out


def test_own_active_connection_is_not_treated_as_foreign_duplicate(tmp_path, fake_bin):
    # 自APが既に上がっていると wifi list にも同じ SSID が載る。それは他ハブではない。
    fake_bin(
        "nmcli",
        'if [[ " $* " == *" --active "* ]]; then echo "presence-hub-ap"; '
        'else echo "presence-hub:70:WPA2"; fi',
    )
    f = _site(tmp_path)
    own = run_bash(
        f'{SOURCE}; site_env_load "{f}"; ap_own_connection_active',
        env=dict(os.environ),
        check=False,
    )
    assert own.returncode == 0
    dup = run_bash(
        f'{SOURCE}; site_env_load "{f}"; ap_duplicate_ssid_present presence-hub',
        env=dict(os.environ),
        check=False,
    )
    assert dup.returncode == 0


def test_apply_explicit_address_fails_when_nmcli_fails(tmp_path, fake_bin):
    fake_bin("nmcli", "exit 1")
    f = _site(tmp_path)
    proc = run_bash(
        f'{SOURCE}; site_env_load "{f}"; ap_apply_explicit_address',
        env=dict(os.environ),
        check=False,
    )
    assert proc.returncode == 1


# 自APが上がっている nmcli。wifi list にも自分の SSID が載る。
_OWN_AP_UP = (
    'if [[ " $* " == *" --active "* ]]; then echo "presence-hub-ap"; '
    'else echo "presence-hub:70:WPA2"; fi'
)


def _plan(tmp_path, args="", extra_env=None):
    f = _site(tmp_path)
    env = dict(os.environ)
    env.pop("AP_FORCE", None)
    env.update(extra_env or {})
    return run_bash(f'{SOURCE}; site_env_load "{f}"; ap_plan {args}',
                    env=env, check=False).stdout.strip()


def test_running_ap_is_skipped_without_force(tmp_path, fake_bin):
    fake_bin("nmcli", _OWN_AP_UP)
    assert _plan(tmp_path) == "skip"


def test_wizard_password_redo_rebuilds_the_running_ap(tmp_path, fake_bin):
    """ウィザードの p は AP_FORCE=1 で 50-ap.sh を呼ぶ。

    以前の 50-ap.sh は AP_FORCE を見ず、しかも AP が動いていれば
    「スキップします」で終わっていた。/etc の secrets と .kit/ap-join.env は
    新しいパスワードになるのに、実際の AP は古いパスワードのまま。
    子は新しいパスワードで繋ぎに行って弾かれ続ける。
    """
    fake_bin("nmcli", _OWN_AP_UP)
    assert _plan(tmp_path, extra_env={"AP_FORCE": "1"}) == "build"


def test_force_flag_rebuilds_the_running_ap(tmp_path, fake_bin):
    fake_bin("nmcli", _OWN_AP_UP)
    assert _plan(tmp_path, args="--force") == "build"


def test_foreign_same_ssid_still_aborts_without_force(tmp_path, fake_bin):
    fake_bin("nmcli", 'if [[ " $* " == *" --active "* ]]; then :; '
                      'else echo "presence-hub:70:WPA2"; fi')
    assert _plan(tmp_path) == "abort"


def test_nothing_up_and_nothing_seen_builds(tmp_path, fake_bin):
    fake_bin("nmcli", ":")
    assert _plan(tmp_path) == "build"


def test_ap_setup_leaves_the_home_wifi_autoconnect_alone(tmp_path):
    """HOME_SSID(F66)は遠隔操作の命綱。AP を作るついでに切ってはいけない。

    以前はフェーズ50 が UFI_CONN=$HOME_SSID を渡し、setup-dongle-ap.sh が
    その接続の autoconnect を no にしていた。デスクトップから F66 に
    繋いだ新機では接続名が SSID と同じになるので、ウィザード最後の
    再起動のあと F66 へ自動で戻らず、遠隔から触れなくなる。
    UFI_CONN はドングルを子機にしていた頃の旧構成向けで、ハブには要らない。
    """
    f = _site(tmp_path, SITE.replace("HOME_SSID=UFI_103134", "HOME_SSID=F660P-sDcS-G"))
    out = run_bash(f'{SOURCE}; site_env_load "{f}"; ap_env_args',
                   env=dict(os.environ)).stdout
    assert "F660P-sDcS-G" not in out
    assert "UFI_CONN=\n" in out + "\n"          # 空で渡す(既定値に落とさない)


def _run_setup_dongle_ap(tmp_path, fake_bin, ufi_env):
    """setup-dongle-ap.sh を偽の root(user namespace)と偽コマンドで流す。

    本物の NetworkManager には触らない: nmcli は偽物で、念のため
    system bus も存在しないパスへ向ける。
    """
    fake_bin("nmcli", 'printf "nmcli %s\\n" "$*" >> "$FAKE_LOG"')
    fake_bin("ip", ":")
    fake_bin("iw", 'echo "        * AP"')
    fake_bin("cat", 'echo phy9')
    fake_bin("sleep", ":")
    fake_bin("pgrep", ":")
    env = dict(os.environ)
    env.update({
        "DBUS_SYSTEM_BUS_ADDRESS": "unix:path=/nonexistent",
        "AP_PSK": "ap-secret9", "AP_SSID": "t-hub", "AP_CONN": "t-hub-ap",
        "SECRETS_ENV": str(tmp_path / "none.env"),
        **ufi_env,
    })
    run_bash("unshare -r bash desktop/presence-tools/setup-dongle-ap.sh",
             env=env, check=False)
    return fake_bin.log.read_text(encoding="utf-8")


def test_setup_dongle_ap_skips_autoconnect_change_when_ufi_conn_is_empty(tmp_path, fake_bin):
    log = _run_setup_dongle_ap(tmp_path, fake_bin, {"UFI_CONN": ""})
    assert "connection add" in log                 # 偽 root で本体まで進んだこと
    assert "autoconnect no" not in log


def test_setup_dongle_ap_keeps_legacy_default_when_run_by_hand(tmp_path, fake_bin):
    """手で直接叩く旧来の使い方(UFI_CONN 未設定)は今までどおり。"""
    env_log = _run_setup_dongle_ap(tmp_path, fake_bin, {})
    assert "connection modify UFI_103134 connection.autoconnect no" in env_log


def test_wizard_force_still_refuses_a_foreign_ap_with_the_same_ssid(tmp_path, fake_bin):
    """AP_FORCE=1 は「自APを作り直す」ためのもの。同名の他ハブを黙認しない。

    自APが落ちていて同じ SSID が見えるなら、それは別のハブ。ウィザードの p で
    作ってしまうと、子がどちらに繋ぐか不定になる二重APができる。
    明示の --force だけが、この確認を越えられる。
    """
    fake_bin("nmcli", 'if [[ " $* " == *" --active "* ]]; then :; '
                      'else echo "presence-hub:70:WPA2"; fi')
    assert _plan(tmp_path, extra_env={"AP_FORCE": "1"}) == "abort"
    assert _plan(tmp_path, args="--force") == "build"


# ---------------------------------------------------------------------------
# AP のサブネットが自機の別経路と重なるときは、起動する前に止める(設計 §2.3)
# ---------------------------------------------------------------------------
from scripts.tests.test_ap_subnet import (  # noqa: E402
    HEALTHY_ADDR,
    HEALTHY_ROUTE,
    INCIDENT_ADDR,
    INCIDENT_ROUTE,
    _fake_ip,
    addr_lines,
)

_NOTHING_UP = ":"    # 自APは落ちていて、同名APも見えない
_AP_ADDR_OK = addr_lines((4, "wlan1", "{gw}/24"))


def _main(tmp_path, fake_bin, *, route, addr, ap_if_addr="", gw="10.42.0.1",
          args="", nmcli=_NOTHING_UP, extra_env=None):
    """50-ap.sh の main を丸ごと流す。setup-dongle-ap.sh と ip は偽物に差し替える。"""
    site = SITE.replace("AP_GW_IP=10.42.0.1", f"AP_GW_IP={gw}")
    site += textwrap.dedent("""\
        HUB_HOSTNAME=presence-hub-2
        HUB_MODE=1
        FACTORY_SSID=HIME-H-REAP
        FACTORY_IP=172.22.13.18/24
        FACTORY_GW=172.22.13.1
        FACTORY_DNS=10.166.1.70
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
        PARENT_STA_NO1=997
        PARENT_STA_NO2=996
        PARENT_STA_NO3=995
        ADMIN_SSID=F660P-sDcS-A
        """)
    (tmp_path / "site.env").write_text(site, encoding="utf-8")
    setup = tmp_path / "fake-setup-dongle-ap.sh"
    setup.write_text('echo "setup-dongle-ap called" >> "$FAKE_LOG"\n', encoding="utf-8")
    fake_bin("nmcli", nmcli)
    env = dict(os.environ)
    env.pop("AP_FORCE", None)
    env.update({
        "SITE_ENV_REPO_DIR": str(tmp_path),
        "CHILDREN_CONF": str(tmp_path / "none.conf"),
        "AP_SETUP_SCRIPT": str(setup),
        "AP_SUBNET_IP_CMD": str(_fake_ip(tmp_path, route, addr, ap_if_addr)),
        **(extra_env or {}),
    })
    return run_bash(f'{SOURCE}; main {args}', env=env, check=False)


def _setup_calls(fake_bin):
    return fake_bin.log.read_text(encoding="utf-8").count("setup-dongle-ap called")


def test_overlap_stops_before_starting_the_ap(tmp_path, fake_bin):
    proc = _main(tmp_path, fake_bin, route=INCIDENT_ROUTE, addr=INCIDENT_ADDR)
    assert proc.returncode != 0
    assert _setup_calls(fake_bin) == 0            # AP を起動していない


def test_overlap_is_not_overridable_by_force(tmp_path, fake_bin):
    """重なった状態は常に誤り。--force も AP_FORCE=1 も越えられない。"""
    for args, env in (("--force", None), ("", {"AP_FORCE": "1"})):
        proc = _main(tmp_path, fake_bin, route=INCIDENT_ROUTE, addr=INCIDENT_ADDR,
                     args=args, extra_env=env)
        assert proc.returncode != 0, (args, env)
    assert _setup_calls(fake_bin) == 0


def test_overlap_stops_even_when_own_ap_is_already_up(tmp_path, fake_bin):
    """自APが動いていて skip になる場合でも、先にチェックする。壊れている最中だから。"""
    proc = _main(tmp_path, fake_bin, route=INCIDENT_ROUTE, addr=INCIDENT_ADDR,
                 nmcli=_OWN_AP_UP)
    assert proc.returncode != 0
    assert "起動しません" in proc.stderr
    assert "既に起動しています" not in proc.stdout


def test_overlap_message_names_the_conflict_the_candidate_and_the_rerun(tmp_path, fake_bin):
    proc = _main(tmp_path, fake_bin, route=INCIDENT_ROUTE, addr=INCIDENT_ADDR)
    err = proc.stderr
    assert "wlan0" in err                                # 重なっている相手
    assert "10.42.0.0/24" in err
    assert "10.42.1.1" in err                            # 空き候補
    assert "bootstrap-hub.sh 50 70" in err               # やり直しの入口


def test_ap_is_verified_to_hold_its_address_after_start(tmp_path, fake_bin):
    """「起動できた」と「効いている」は別物。既定値のときも wlan1 のアドレスを確認する。"""
    proc = _main(tmp_path, fake_bin, route=HEALTHY_ROUTE, addr=HEALTHY_ADDR, ap_if_addr="")
    assert _setup_calls(fake_bin) == 1
    assert proc.returncode != 0
    ok = _main(tmp_path, fake_bin, route=HEALTHY_ROUTE, addr=HEALTHY_ADDR,
               ap_if_addr=_AP_ADDR_OK.format(gw="10.42.0.1"))
    assert ok.returncode == 0, ok.stderr


def test_duplicate_ssid_message_does_not_tell_to_change_ap_gw_ip(tmp_path, fake_bin):
    """AP同士は孤立しているので、増設でも AP_GW_IP を別にする必要はない(設計 §2.1)。"""
    proc = _main(tmp_path, fake_bin, route=HEALTHY_ROUTE, addr=HEALTHY_ADDR,
                 nmcli='if [[ " $* " == *" --active "* ]]; then :; '
                       'else echo "presence-hub:70:WPA2"; fi')
    assert proc.returncode != 0
    assert "AP_SSID を別の値" in proc.stderr
    assert "AP_GW_IP" not in proc.stderr


def test_non_default_gateway_warning_points_at_the_align_tooling(tmp_path, fake_bin):
    proc = _main(tmp_path, fake_bin, route=HEALTHY_ROUTE, addr=HEALTHY_ADDR,
                 gw="10.42.1.1", ap_if_addr=_AP_ADDR_OK.format(gw="10.42.1.1"))
    assert proc.returncode == 0, proc.stderr
    assert "fleet_ui.child_cli align" in proc.stdout
    assert "変更する必要があります" not in proc.stdout
