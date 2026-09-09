"""site.env の読込と検証を検証する。

機体固有値の取り違えは「動くが壊れている」形で現れる(固定IP衝突・STA_NO重複)。
起動前にここで弾くのが唯一の防波堤なので、検証の穴はそのまま事故になる。
"""
import os
import textwrap

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/lib/site-env.sh"

VALID = textwrap.dedent("""\
    HUB_HOSTNAME=presence-hub-2
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


def _write(tmp_path, body=VALID, children="zero2\n"):
    env_file = tmp_path / "site.env"
    env_file.write_text(body, encoding="utf-8")
    inv = tmp_path / "children.conf"
    inv.write_text(children, encoding="utf-8")
    return env_file, inv


def _run(env_file, inv, check=False):
    env = dict(os.environ)
    env["CHILDREN_CONF"] = str(inv)
    return run_bash(
        f'{SOURCE}; site_env_load "{env_file}" && site_env_validate',
        env=env, check=check,
    )


def test_valid_file_passes(tmp_path):
    env_file, inv = _write(tmp_path)
    assert _run(env_file, inv).returncode == 0


def test_missing_file_fails_with_path_in_message(tmp_path):
    proc = run_bash(
        f'{SOURCE}; site_env_load "{tmp_path}/nope.env"',
        env=dict(os.environ), check=False,
    )
    assert proc.returncode != 0
    assert "nope.env" in proc.stderr


def test_missing_required_variable_names_it(tmp_path):
    body = VALID.replace("PARENT_STA_NO2=996\n", "")
    env_file, inv = _write(tmp_path, body=body)
    proc = _run(env_file, inv)
    assert proc.returncode != 0
    assert "PARENT_STA_NO2" in proc.stderr


def test_factory_ip_without_prefix_is_rejected(tmp_path):
    body = VALID.replace("FACTORY_IP=172.22.13.18/24", "FACTORY_IP=172.22.13.18")
    env_file, inv = _write(tmp_path, body=body)
    proc = _run(env_file, inv)
    assert proc.returncode != 0
    assert "FACTORY_IP" in proc.stderr


def test_hostname_colliding_with_a_child_is_rejected(tmp_path):
    # 子と同名だと device_id と MQTT client_id が衝突し、2台が互いを蹴り合う
    body = VALID.replace("HUB_HOSTNAME=presence-hub-2", "HUB_HOSTNAME=zero2")
    env_file, inv = _write(tmp_path, body=body, children="zero2\n")
    proc = _run(env_file, inv)
    assert proc.returncode != 0
    assert "zero2" in proc.stderr


def test_ap_gateway_inside_the_factory_subnet_is_rejected(tmp_path):
    # 子APを工場網と同じ /24 に置くと、経路が二重になり Oracle へ届かなくなる
    body = VALID.replace("AP_GW_IP=10.42.0.1", "AP_GW_IP=172.22.13.1")
    env_file, inv = _write(tmp_path, body=body)
    proc = _run(env_file, inv)
    assert proc.returncode != 0
    assert "AP_GW_IP" in proc.stderr


def test_ap_gateway_inside_a_wider_factory_prefix_is_rejected(tmp_path):
    # FACTORY_IP が /16 のとき、先頭3オクテット比較では素通りしてしまう組が実在する
    # (172.22.5.x は 172.22.0.0/16 の内側だが 172.22.0 と 172.22.5 は文字列として不一致)
    body = VALID.replace("FACTORY_IP=172.22.13.18/24", "FACTORY_IP=172.22.0.5/16")
    body = body.replace("AP_GW_IP=10.42.0.1", "AP_GW_IP=172.22.5.1")
    env_file, inv = _write(tmp_path, body=body)
    proc = _run(env_file, inv)
    assert proc.returncode != 0
    assert "AP_GW_IP" in proc.stderr


def test_ap_gateway_outside_a_wide_factory_prefix_still_passes(tmp_path):
    # /16 でも本当にサブネット外なら誤検出せずに通す
    body = VALID.replace("FACTORY_IP=172.22.13.18/24", "FACTORY_IP=172.22.0.5/16")
    env_file, inv = _write(tmp_path, body=body)
    assert _run(env_file, inv).returncode == 0


def test_ap_gateway_in_the_other_half_of_a_narrower_prefix_passes(tmp_path):
    # /25 のとき、同じ /24 でももう半分 (172.22.13.128-255) は別サブネットなので通す
    body = VALID.replace("FACTORY_IP=172.22.13.18/24", "FACTORY_IP=172.22.13.18/25")
    body = body.replace("AP_GW_IP=10.42.0.1", "AP_GW_IP=172.22.13.200")
    env_file, inv = _write(tmp_path, body=body)
    assert _run(env_file, inv).returncode == 0


def test_malformed_factory_ip_does_not_crash_the_subnet_check(tmp_path):
    # プレフィックス無しは CIDR エラーで弾かれるべきだが、後続のサブネット判定に
    # そのまま "${FACTORY_IP#*/}" (=IP全体) を渡すと bash の算術構文エラーが
    # 生の内部エラーとして stderr に漏れる。CIDR エラーだけが出て、bash の
    # 内部エラーは出ないことを確認する。
    body = VALID.replace("FACTORY_IP=172.22.13.18/24", "FACTORY_IP=172.22.13.18")
    env_file, inv = _write(tmp_path, body=body)
    proc = _run(env_file, inv)
    assert proc.returncode != 0
    assert "FACTORY_IP" in proc.stderr
    assert "syntax error" not in proc.stderr
    assert "arithmetic" not in proc.stderr


def test_example_file_is_itself_valid(tmp_path):
    # ひな型が検証を通らないと、利用者は最初の一歩で詰まる
    inv = tmp_path / "children.conf"
    inv.write_text("zero2\n", encoding="utf-8")
    env = dict(os.environ)
    env["CHILDREN_CONF"] = str(inv)
    proc = run_bash(
        f'{SOURCE}; site_env_load site.env.example && site_env_validate',
        env=env, check=False,
    )
    assert proc.returncode == 0, proc.stderr
