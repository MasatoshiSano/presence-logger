"""USB から新機へコピーする処理の検証。

FAT 上では実行ビットが落ち、所有者も崩れる。「権限がなくて開けない」を
コピー直後に潰すのがこのスクリプトの仕事。
"""
import os
import stat

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/copy-hub-from-usb.sh"


def _env(extra=None):
    env = dict(os.environ)
    if extra:
        env.update(extra)
    return env


def test_fix_permissions_restores_exec_on_scripts_and_desktop(tmp_path):
    dest = tmp_path / "presence-logger"
    dest.mkdir()
    sh = dest / "run.sh"
    sh.write_text("#!/bin/bash\necho hi\n", encoding="utf-8")
    sh.chmod(0o644)
    desk = dest / "x.desktop"
    desk.write_text("[Desktop Entry]\nName=x\n", encoding="utf-8")
    desk.chmod(0o644)
    run_bash(f'{SOURCE}; copy_fix_permissions "{dest}"', env=_env())
    assert sh.stat().st_mode & stat.S_IXUSR
    assert desk.stat().st_mode & stat.S_IXUSR


def test_copy_installs_setup_icon_and_does_not_enable_systemd(tmp_path, fake_bin):
    fake_bin("systemctl", 'echo systemctl "$@" >> "$FAKE_LOG"; exit 1')
    kit = tmp_path / "presence-hub-kit"
    payload = kit / "payload" / "presence-logger"
    payload.mkdir(parents=True)
    (payload / "hello.txt").write_text("copied\n", encoding="utf-8")
    (kit / ".kit").mkdir()
    (kit / ".kit" / "origin.env").write_text("ORIGIN_HOSTNAME=parent\n", encoding="utf-8")
    dest = tmp_path / "projects" / "presence-logger"
    desk = tmp_path / "Desktop"
    desk.mkdir()
    launchers = payload / "desktop" / "launchers"
    launchers.mkdir(parents=True)
    (launchers / "ハブ初期設定.desktop").write_text(
        "[Desktop Entry]\nName=ハブ初期設定\nExec=bash __REPO_DIR__/scripts/setup-hub-wizard.sh\n",
        encoding="utf-8",
    )
    proc = run_bash(
        f'{SOURCE}; copy_hub_from_usb "{kit}" "{dest}" "{desk}"',
        env=_env(),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (dest / "hello.txt").read_text(encoding="utf-8") == "copied\n"
    assert (dest / ".kit" / "origin.env").is_file()
    icon = desk / "ハブ初期設定.desktop"
    assert icon.is_file()
    body = icon.read_text(encoding="utf-8")
    assert "__REPO_DIR__" not in body
    assert str(dest) in body
    assert icon.stat().st_mode & stat.S_IXUSR
    log = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "systemctl" not in log


def test_copy_finds_kit_by_origin_env(tmp_path):
    media = tmp_path / "media" / "usb"
    kit = media / "presence-hub-kit"
    kit.mkdir(parents=True)
    (kit / ".kit").mkdir()
    (kit / ".kit" / "origin.env").write_text("ORIGIN_HOSTNAME=p\n", encoding="utf-8")
    found = run_bash(
        f'{SOURCE}; copy_find_kit "{tmp_path / "media"}"',
        env=_env(),
    ).stdout.strip()
    assert found == str(kit)
