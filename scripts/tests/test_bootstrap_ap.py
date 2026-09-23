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
