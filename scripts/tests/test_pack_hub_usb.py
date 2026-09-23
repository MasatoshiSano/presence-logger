"""USB キット作成 (pack-hub-usb.sh) の検証。

キットに親の children.conf や Oracle パスワードが乗ると、新機が親と同じ子を
奪い合う / 秘密が USB に残る。載せないものの一覧はテストで固定する。
"""
import os
import stat
import textwrap

import pytest

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
            FACTORY_SSID=HIME-H-REAP
            FACTORY_GW=172.22.13.1
            FACTORY_DNS=10.166.1.70,10.166.1.17
            ORACLE_HOST=10.166.5.93
            ORACLE_PORT=1521
            ORACLE_SERVICE=HHC001
            ORACLE_USER=ZHH001
            ORACLE_TABLE=HF1RCM01
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
    assert "ORIGIN_FACTORY_SSID=HIME-H-REAP" in body
    assert "ORIGIN_FACTORY_GW=172.22.13.1" in body
    assert "ORIGIN_ORACLE_HOST=10.166.5.93" in body
    assert "ORIGIN_ORACLE_PORT=1521" in body


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
            # PSK なしは既定で中止になる。ここで見たいのは実行ビットの話なので明示的に通す。
            "PACK_ALLOW_NO_PSK": "1",
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
    # PACK_SECRETS を固定しないと、実機の /etc/presence-logger/secrets.env を
    # 読みにいく。あの実体は root:docker 600 なので、テストの結果が
    # 「どのマシンで走らせたか」で変わってしまう。
    host_secrets = tmp_path / "secrets.env"
    host_secrets.write_text(
        "ORACLE_PASSWORD_HHC=super-secret\nWIFI_PSK_HIMEREAP=factory-psk\n",
        encoding="utf-8",
    )
    run_bash(
        f'{SOURCE}; pack_hub_kit "{src}" "{dest}"',
        env=_env({
            "PACK_SKIP_DOCKER": "1",
            "PACK_SITE_ENV": str(site),
            "PACK_SECRETS": str(host_secrets),
        }),
    )
    secrets = (dest / "presence-hub-kit" / ".kit" / "secrets.env.template").read_text(
        encoding="utf-8"
    )
    assert "ORACLE_PASSWORD" not in secrets or "ORACLE_PASSWORD_HHC=" not in secrets
    # 実ファイルのパス確認用。REPO_ROOT はテスト実行場所
    assert REPO_ROOT.is_dir()


# --- secrets.env が読めないとき (2026-09-23 実機検証で発見) ---------------
#
# 実機の /etc/presence-logger/secrets.env は root:docker の 600。手順書どおり
# pi で pack すると grep が Permission denied を出すが、`|| true` に吸われて
# 0バイトのテンプレを書いたまま「✅」で終わっていた。工場WiFi の PSK が
# 載らないキットが現場へ出て、新機が工場網へ繋がろうとした段になって初めて
# 失敗する。クラウドのテストは stdin 経由で呼んでいたため永久に緑だった。

_as_root = os.geteuid() == 0
skip_if_root = pytest.mark.skipif(
    _as_root, reason="root は mode 000 も読めるので権限分岐を再現できない"
)


def _unreadable(tmp_path):
    p = tmp_path / "secrets.env"
    p.write_text(
        "ORACLE_PASSWORD_HHC=super-secret\nWIFI_PSK_HIMEREAP=factory-psk\n",
        encoding="utf-8",
    )
    p.chmod(0o000)
    return p


def test_pack_read_secrets_reports_missing_file(tmp_path):
    proc = run_bash(
        f'{SOURCE}; pack_read_secrets "{tmp_path}/none.env"',
        env=_env(),
        check=False,
    )
    assert proc.returncode == 2, "無いファイルと読めないファイルは区別する"


def test_pack_read_secrets_reads_directly_when_permitted(tmp_path):
    p = tmp_path / "secrets.env"
    p.write_text("WIFI_PSK_HIMEREAP=factory-psk\n", encoding="utf-8")
    proc = run_bash(f'{SOURCE}; pack_read_secrets "{p}"', env=_env(), check=True)
    assert "WIFI_PSK_HIMEREAP=factory-psk" in proc.stdout


@skip_if_root
def test_pack_read_secrets_escalates_to_sudo_when_unreadable(tmp_path, fake_bin):
    p = _unreadable(tmp_path)
    # 本物の sudo を呼ばせない。偽 sudo は pi のままなので mode 000 を
    # 読めない。root が読めたときの出力をそのまま返して代役にする。
    fake_bin(
        "sudo",
        'printf "ORACLE_PASSWORD_HHC=super-secret\\nWIFI_PSK_HIMEREAP=factory-psk\\n"',
    )
    proc = run_bash(f'{SOURCE}; pack_read_secrets "{p}"', env=_env(), check=False)
    assert proc.returncode == 0, proc.stderr
    assert "WIFI_PSK_HIMEREAP=factory-psk" in proc.stdout


@skip_if_root
def test_pack_fails_instead_of_writing_empty_secrets_template(tmp_path, fake_bin):
    """sudo も通らないなら、空テンプレの USB を作らずに止まること。"""
    src = tmp_path / "src"
    (src / "scripts").mkdir(parents=True)
    dest = tmp_path / "usb"
    site = tmp_path / "site.env"
    site.write_text(
        "HUB_HOSTNAME=parent\nFACTORY_IP=1.2.3.4/24\nAP_SSID=old\n"
        "PARENT_STA_NO1=1\nPARENT_STA_NO2=2\nPARENT_STA_NO3=3\n",
        encoding="utf-8",
    )
    p = _unreadable(tmp_path)
    fake_bin("sudo", "exit 1")  # sudo が拒否される環境
    proc = run_bash(
        f'{SOURCE}; pack_hub_kit "{src}" "{dest}"',
        env=_env({
            "PACK_SKIP_DOCKER": "1",
            "PACK_SITE_ENV": str(site),
            "PACK_SECRETS": str(p),
        }),
        check=False,
    )
    assert proc.returncode != 0, "読めなかったのに成功で終わってはいけない"
    assert "✅" not in proc.stdout, "失敗したのに成功マークを出してはいけない"
    tmpl = dest / "presence-hub-kit" / ".kit" / "secrets.env.template"
    assert not (tmpl.exists() and tmpl.stat().st_size == 0), \
        "0バイトのテンプレを残すと、壊れたキットが現場へ出る"


def _kit_inputs(tmp_path):
    src = tmp_path / "src"
    (src / "scripts").mkdir(parents=True)
    site = tmp_path / "site.env"
    site.write_text(
        "HUB_HOSTNAME=parent\nFACTORY_IP=1.2.3.4/24\nAP_SSID=old\n"
        "PARENT_STA_NO1=1\nPARENT_STA_NO2=2\nPARENT_STA_NO3=3\n",
        encoding="utf-8",
    )
    return src, site


def test_pack_stops_when_factory_psk_would_not_reach_the_new_hub(tmp_path):
    """PSK の無いキットは、現場で「繋がらない」になるまで誰も気づけない。"""
    src, site = _kit_inputs(tmp_path)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("ORACLE_PASSWORD_HHC=only-oracle\n", encoding="utf-8")
    dest = tmp_path / "usb"
    proc = run_bash(
        f'{SOURCE}; pack_hub_kit "{src}" "{dest}"',
        env=_env({
            "PACK_SKIP_DOCKER": "1",
            "PACK_SITE_ENV": str(site),
            "PACK_SECRETS": str(secrets),
        }),
        check=False,
    )
    assert proc.returncode != 0
    assert "✅" not in proc.stdout


def test_pack_allows_no_psk_when_explicitly_requested(tmp_path):
    """別工場へ持っていくなど、PSK を載せない運用は明示的に通す。"""
    src, site = _kit_inputs(tmp_path)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("ORACLE_PASSWORD_HHC=only-oracle\n", encoding="utf-8")
    dest = tmp_path / "usb"
    proc = run_bash(
        f'{SOURCE}; pack_hub_kit "{src}" "{dest}"',
        env=_env({
            "PACK_SKIP_DOCKER": "1",
            "PACK_SITE_ENV": str(site),
            "PACK_SECRETS": str(secrets),
            "PACK_ALLOW_NO_PSK": "1",
        }),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "PACK_ALLOW_NO_PSK" in proc.stderr


def test_driver_kit_carries_source_but_not_build_artifacts(tmp_path):
    """親機のカーネル向けビルド成果物を新機へ持ち込まない。

    make はソースより新しい .o を見ると再コンパイルを省くので、残留物が
    あると新機のカーネル向けに組み直されず、vermagic 不一致の .ko が出来る。
    """
    src = tmp_path / "8821au"
    (src / "os_dep" / "linux").mkdir(parents=True)
    (src / ".git").mkdir()
    (src / "os_dep" / "linux" / "usb_intf.c").write_text("src\n", encoding="utf-8")
    (src / "Makefile").write_text("all:\n", encoding="utf-8")
    (src / "install-driver.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (src / "8821au.ko").write_text("stale module\n", encoding="utf-8")
    (src / "8821au.mod.c").write_text("stale\n", encoding="utf-8")
    (src / "Module.symvers").write_text("stale\n", encoding="utf-8")
    (src / "modules.order").write_text("stale\n", encoding="utf-8")
    (src / "os_dep" / "linux" / "usb_intf.o").write_text("stale obj\n", encoding="utf-8")
    (src / "os_dep" / "linux" / ".usb_intf.o.cmd").write_text("stale cmd\n", encoding="utf-8")
    (src / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    dest = tmp_path / "kit-driver"
    run_bash(f'{SOURCE}; pack_copy_driver "{src}" "{dest}"', env=_env())

    assert (dest / "os_dep" / "linux" / "usb_intf.c").is_file(), "ソースは載せる"
    assert (dest / "Makefile").is_file()
    assert (dest / "install-driver.sh").is_file()
    for stale in (
        "8821au.ko",
        "8821au.mod.c",
        "Module.symvers",
        "modules.order",
        "os_dep/linux/usb_intf.o",
        "os_dep/linux/.usb_intf.o.cmd",
        ".git/HEAD",
    ):
        assert not (dest / stale).exists(), f"{stale} を新機へ持ち込んではいけない"
