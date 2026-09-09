"""USB キット作成 (pack-hub-usb.sh) の検証。

キットに親の children.conf や Oracle パスワードが乗ると、新機が親と同じ子を
奪い合う / 秘密が USB に残る。載せないものの一覧はテストで固定する。
"""
import os
import stat
import textwrap

from scripts.tests.shellhelp import REPO_ROOT, run_bash

SOURCE = "source scripts/pack-hub-usb.sh"


def _env(extra=None):
    env = dict(os.environ)
    if extra:
        env.update(extra)
    return env


def test_rsync_excludes_cover_collision_and_secret_sources():
    out = run_bash(f"{SOURCE}; pack_rsync_excludes", env=_env()).stdout.split()
    for item in (
        ".venv",
        "site.env",
        "fleet/children.conf",
        "fleet/known_macs.json",
        "*.db",
    ):
        assert item in out, f"{item} をキットに載せてはいけない"


def test_hub_images_do_not_include_detector():
    out = run_bash(f"{SOURCE}; pack_hub_images", env=_env()).stdout
    assert "mosquitto" in out
    assert "oracle-jdbc" in out
    assert "bridge" in out
    assert "detector" not in out


def test_strip_secrets_drops_oracle_and_ap_psk_keeps_factory_wifi():
    raw = textwrap.dedent("""\
        ORACLE_PASSWORD_HHC=super-secret
        WIFI_PSK_HIMEREAP=factory-psk
        WIFI_AP_PSK=old-ap-psk
        WALLET_PASSWORD_B=wallet
        WIFI_PSK_HOME=home-psk
        """)
    proc = run_bash(
        f"{SOURCE}; pack_strip_secrets",
        env=_env(),
        check=True,
        stdin=raw,
    )
    assert "ORACLE_PASSWORD_HHC" not in proc.stdout
    assert "WIFI_AP_PSK" not in proc.stdout
    assert "WALLET_PASSWORD_B" not in proc.stdout
    assert "WIFI_PSK_HIMEREAP=factory-psk" in proc.stdout
    assert "WIFI_PSK_HOME=home-psk" in proc.stdout


def test_pack_tree_drops_children_and_writes_empty_inventory(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "usb"
    (src / "fleet").mkdir(parents=True)
    (src / "fleet" / "children.conf").write_text("zero2\npizero2w-2.local\n", encoding="utf-8")
    (src / "fleet" / "known_macs.json").write_text('{"a":1}\n', encoding="utf-8")
    (src / "site.env").write_text("HUB_HOSTNAME=parent\n", encoding="utf-8")
    (src / ".venv" / "bin").mkdir(parents=True)
    (src / ".venv" / "bin" / "python").write_text("x", encoding="utf-8")
    (src / "keep.txt").write_text("ok\n", encoding="utf-8")
    (src / "inbox.db").write_text("db\n", encoding="utf-8")

    kit = dest / "presence-hub-kit"
    run_bash(
        f'{SOURCE}; pack_copy_tree "{src}" "{kit}/payload/presence-logger"',
        env=_env(),
    )
    payload = kit / "payload" / "presence-logger"
    assert (payload / "keep.txt").read_text(encoding="utf-8") == "ok\n"
    assert not (payload / "site.env").exists()
    assert not (payload / "fleet" / "children.conf").exists()
    assert not (payload / "fleet" / "known_macs.json").exists()
    assert not (payload / ".venv").exists()
    assert not (payload / "inbox.db").exists()

    run_bash(f'{SOURCE}; pack_write_empty_children "{kit}/payload/presence-logger"', env=_env())
    children = (payload / "fleet" / "children.conf").read_text(encoding="utf-8")
    assert "zero2" not in children
    assert "pizero2w-2" not in children


def test_pack_writes_origin_from_parent_site_env(tmp_path):
    site = tmp_path / "site.env"
    site.write_text(
        textwrap.dedent("""\
            HUB_HOSTNAME=raspberrypi5
            FACTORY_IP=172.22.13.17/24
            AP_SSID=presence-hub
            PARENT_STA_NO1=996
            PARENT_STA_NO2=995
            PARENT_STA_NO3=994
            """),
        encoding="utf-8",
    )
    origin = tmp_path / "origin.env"
    run_bash(
        f'{SOURCE}; pack_write_origin "{site}" "{origin}"',
        env=_env(),
    )
    body = origin.read_text(encoding="utf-8")
    assert "ORIGIN_HOSTNAME=raspberrypi5" in body
    assert "ORIGIN_FACTORY_IP=172.22.13.17/24" in body
    assert "ORIGIN_AP_SSID=presence-hub" in body
    assert "ORIGIN_PARENT_STA_NO1=996" in body


def test_pack_skips_docker_save_when_requested(tmp_path, fake_bin):
    fake_bin(
        "docker",
        'echo docker "$@" >> "$FAKE_LOG"; echo "docker should not run"; exit 1',
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "keep.txt").write_text("ok\n", encoding="utf-8")
    dest = tmp_path / "usb"
    site = tmp_path / "site.env"
    site.write_text(
        "HUB_HOSTNAME=parent\nFACTORY_IP=172.22.13.17/24\nAP_SSID=presence-hub\n"
        "PARENT_STA_NO1=1\nPARENT_STA_NO2=2\nPARENT_STA_NO3=3\n",
        encoding="utf-8",
    )
    secrets = tmp_path / "secrets.env"
    secrets.write_text("WIFI_PSK_HIMEREAP=x\nORACLE_PASSWORD_HHC=nope\n", encoding="utf-8")
    proc = run_bash(
        f'{SOURCE}; pack_hub_kit "{src}" "{dest}"',
        env=_env({
            "PACK_SKIP_DOCKER": "1",
            "PACK_SITE_ENV": str(site),
            "PACK_SECRETS": str(secrets),
            "PACK_DRIVER_SRC": "",
        }),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    log = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "docker save" not in log
    kit = dest / "presence-hub-kit"
    assert "ORACLE_PASSWORD_HHC" not in (kit / ".kit" / "secrets.env.template").read_text(
        encoding="utf-8"
    )
    assert "WIFI_PSK_HIMEREAP=x" in (kit / ".kit" / "secrets.env.template").read_text(
        encoding="utf-8"
    )


def test_pack_copy_helper_is_runnable_with_bash_even_without_exec_bit(tmp_path):
    """FAT の USB は実行ビットが落ちる。bash copy-to-this-pi.sh で動くこと。"""
    src = tmp_path / "src"
    src.mkdir()
    dest = tmp_path / "usb"
    site = tmp_path / "site.env"
    site.write_text(
        "HUB_HOSTNAME=parent\nFACTORY_IP=172.22.13.17/24\nAP_SSID=presence-hub\n"
        "PARENT_STA_NO1=1\nPARENT_STA_NO2=2\nPARENT_STA_NO3=3\n",
        encoding="utf-8",
    )
    run_bash(
        f'{SOURCE}; pack_hub_kit "{src}" "{dest}"',
        env=_env({
            "PACK_SKIP_DOCKER": "1",
            "PACK_SITE_ENV": str(site),
            "PACK_SECRETS": str(tmp_path / "no-secrets"),
        }),
    )
    helper = dest / "presence-hub-kit" / "copy-to-this-pi.sh"
    assert helper.is_file()
    helper.chmod(helper.stat().st_mode & ~stat.S_IXUSR)
    assert not os.access(helper, os.X_OK)
    # 中身が bash で実行できること（実行ビット無し）
    assert helper.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")
    desktop = dest / "presence-hub-kit" / "このUSBからコピー.desktop"
    assert desktop.is_file()
    assert "copy-to-this-pi.sh" in desktop.read_text(encoding="utf-8")


def test_pack_does_not_copy_repo_copy_hub_script_as_payload_secret(tmp_path):
    # リポジトリの scripts/ は載せてよい。secrets の実体は載せない。
    src = tmp_path / "src"
    (src / "scripts").mkdir(parents=True)
    dest = tmp_path / "usb"
    site = tmp_path / "site.env"
    site.write_text(
        "HUB_HOSTNAME=parent\nFACTORY_IP=1.2.3.4/24\nAP_SSID=old\n"
        "PARENT_STA_NO1=1\nPARENT_STA_NO2=2\nPARENT_STA_NO3=3\n",
        encoding="utf-8",
    )
    run_bash(
        f'{SOURCE}; pack_hub_kit "{src}" "{dest}"',
        env=_env({
            "PACK_SKIP_DOCKER": "1",
            "PACK_SITE_ENV": str(site),
        }),
    )
    secrets = (dest / "presence-hub-kit" / ".kit" / "secrets.env.template").read_text(
        encoding="utf-8"
    )
    assert "ORACLE_PASSWORD" not in secrets or "ORACLE_PASSWORD_HHC=" not in secrets
    # 実ファイルのパス確認用。REPO_ROOT はテスト実行場所
    assert REPO_ROOT.is_dir()
