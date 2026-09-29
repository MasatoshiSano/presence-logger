"""USB から新機へコピーする処理の検証。

FAT 上では実行ビットが落ち、所有者も崩れる。「権限がなくて開けない」を
コピー直後に潰すのがこのスクリプトの仕事。
"""
import os
import pwd
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


# ---- コピー後のハブ初期設定の自動起動（設計: docs/2026-09-29-copy-autolaunch-design.md）----

def test_sourcing_has_no_side_effects(tmp_path):
    # scripts/bootstrap/70-desktop.sh は関数を使うためにこのファイルを source する。
    wiz_log = tmp_path / "wiz.log"
    fake = tmp_path / "fake-wizard.sh"
    fake.write_text(f'#!/usr/bin/env bash\necho ran >> "{wiz_log}"\n', encoding="utf-8")
    fake.chmod(0o755)
    proc = run_bash(
        f"{SOURCE}; echo ok",
        env=_env({"COPY_FORCE_TTY": "1", "COPY_WIZARD_CMD": str(fake)}),
    )
    assert proc.stdout.strip() == "ok"
    assert not wiz_log.exists()


def test_as_user_prefix_root_drops_sudo_env():
    out = run_bash(f"{SOURCE}; copy_as_user_prefix 0 pi /home/pi", env=_env()).stdout.split()
    assert out == [
        "runuser", "-u", "pi", "--", "env",
        "-u", "SUDO_USER", "-u", "SUDO_UID", "-u", "SUDO_GID", "-u", "SUDO_COMMAND",
        "HOME=/home/pi", "USER=pi", "LOGNAME=pi",
    ]


def test_as_user_prefix_non_root_is_plain_env():
    out = run_bash(f"{SOURCE}; copy_as_user_prefix 1000 pi /home/pi", env=_env()).stdout.split()
    assert out == ["env", "HOME=/home/pi"]
    assert "runuser" not in out
    assert "sudo" not in out


def _kit(tmp_path, *, preflight=None, payload=True, icon=True):
    """最小キット。payload の setup-hub-wizard.sh は本物ではなく exit 99 のダミー。"""
    kit = tmp_path / "presence-hub-kit"
    (kit / ".kit").mkdir(parents=True)
    (kit / ".kit" / "origin.env").write_text("ORIGIN_HOSTNAME=parent\n", encoding="utf-8")
    if payload:
        pl = kit / "payload" / "presence-logger"
        (pl / "scripts").mkdir(parents=True)
        (pl / "hello.txt").write_text("copied\n", encoding="utf-8")
        (pl / "scripts" / "setup-hub-wizard.sh").write_text("exit 99\n", encoding="utf-8")
        if icon:
            (pl / "desktop" / "launchers").mkdir(parents=True)
            (pl / "desktop" / "launchers" / "ハブ初期設定.desktop").write_text(
                "[Desktop Entry]\nName=x\nExec=bash __REPO_DIR__/scripts/setup-hub-wizard.sh\n",
                encoding="utf-8",
            )
    if preflight is not None:
        (kit / "preflight-new-hub.sh").write_text(preflight + "\n", encoding="utf-8")
    return kit


def _fake_wizard(tmp_path):
    """起動されたときの引数・環境を wiz.log に書く偽ウィザード。"""
    log = tmp_path / "wiz.log"
    fake = tmp_path / "fake-wizard.sh"
    fake.write_text(
        '#!/usr/bin/env bash\n'
        f'printf \'%s\\n\' "$@" "PWD=$PWD" "USER=$USER" "HOME=$HOME" '
        f'"SUDO_USER=${{SUDO_USER-unset}}" > "{log}"\n'
        'exit ${FAKE_WIZ_RC:-0}\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake, log


def _run_main(tmp_path, kit, *, extra=None, stdin=""):
    """main "<kit>" を実行。dest / desktop は tmp_path 配下。SUDO_USER は消す。"""
    env = _env({
        "COPY_DEST": str(tmp_path / "projects" / "presence-logger"),
        "COPY_DESKTOP": str(tmp_path / "Desktop"),
        **(extra or {}),
    })
    env.pop("SUDO_USER", None)
    return run_bash(f'{SOURCE}; main "{kit}"', env=env, check=False, stdin=stdin)


def _dest(tmp_path):
    return tmp_path / "projects" / "presence-logger"


def _mark_presence_logger(d):
    (d / "services" / "bridge").mkdir(parents=True)
    (d / "scripts").mkdir(exist_ok=True)
    (d / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (d / "scripts" / "bootstrap-hub.sh").write_text("#\n", encoding="utf-8")


def test_foreign_dest_refuses_copy(tmp_path):
    dest = _dest(tmp_path)
    dest.mkdir(parents=True)
    (dest / "notes.txt").write_text("mine\n", encoding="utf-8")
    fake, wiz_log = _fake_wizard(tmp_path)
    proc = _run_main(tmp_path, _kit(tmp_path),
                     extra={"COPY_FORCE_TTY": "1", "COPY_WIZARD_CMD": str(fake)})
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert not (dest / "hello.txt").exists()
    assert (dest / "notes.txt").read_text(encoding="utf-8") == "mine\n"
    assert "presence-logger ではない中身" in proc.stderr
    assert not wiz_log.exists()


def test_existing_presence_logger_dest_is_updated(tmp_path):
    dest = _dest(tmp_path)
    _mark_presence_logger(dest)
    proc = _run_main(tmp_path, _kit(tmp_path), extra={"COPY_FORCE_TTY": "0"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (dest / "hello.txt").read_text(encoding="utf-8") == "copied\n"


def test_empty_dest_is_ok(tmp_path):
    dest = _dest(tmp_path)
    dest.mkdir(parents=True)
    proc = _run_main(tmp_path, _kit(tmp_path), extra={"COPY_FORCE_TTY": "0"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (dest / "hello.txt").is_file()


def test_missing_icon_template_fails_loudly(tmp_path):
    fake, wiz_log = _fake_wizard(tmp_path)
    proc = _run_main(tmp_path, _kit(tmp_path, icon=False),
                     extra={"COPY_FORCE_TTY": "1", "COPY_WIZARD_CMD": str(fake)})
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "クリックしてください" not in proc.stdout
    assert "アイコンを置けませんでした" in proc.stderr
    assert not wiz_log.exists()


def _pf(tmp_path, body):
    """呼ばれたことを pf.log に残す偽の事前点検本文。"""
    return f'echo called >> "{tmp_path / "pf.log"}"\n{body}'


def test_no_tty_does_not_launch_wizard(tmp_path):
    fake, wiz_log = _fake_wizard(tmp_path)
    kit = _kit(tmp_path, preflight=_pf(tmp_path, "exit 0"))
    proc = _run_main(tmp_path, kit,
                     extra={"COPY_FORCE_TTY": "0", "COPY_WIZARD_CMD": str(fake)})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not wiz_log.exists()
    assert "「ハブ初期設定」をクリック" in proc.stdout
    assert not (tmp_path / "pf.log").exists()


def test_no_wizard_env_disables_launch(tmp_path):
    fake, wiz_log = _fake_wizard(tmp_path)
    kit = _kit(tmp_path, preflight=_pf(tmp_path, "exit 0"))
    proc = _run_main(tmp_path, kit, extra={
        "COPY_FORCE_TTY": "1", "COPY_NO_WIZARD": "1", "COPY_WIZARD_CMD": str(fake)})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not wiz_log.exists()
    assert "「ハブ初期設定」をクリック" in proc.stdout


def test_setup_complete_skips_launch_and_is_preserved(tmp_path):
    dest = _dest(tmp_path)
    _mark_presence_logger(dest)
    (dest / ".kit").mkdir()
    (dest / ".kit" / "setup-complete").write_text("done-earlier\n", encoding="utf-8")
    fake, wiz_log = _fake_wizard(tmp_path)
    kit = _kit(tmp_path, preflight=_pf(tmp_path, "exit 0"))
    proc = _run_main(tmp_path, kit,
                     extra={"COPY_FORCE_TTY": "1", "COPY_WIZARD_CMD": str(fake)})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not wiz_log.exists()
    assert "設定済み" in proc.stdout
    assert "p を選んで" in proc.stdout
    assert (dest / ".kit" / "setup-complete").read_text(encoding="utf-8") == "done-earlier\n"


def test_root_user_does_not_launch(tmp_path):
    fake, wiz_log = _fake_wizard(tmp_path)
    kit = _kit(tmp_path, preflight=_pf(tmp_path, "exit 0"))
    dest = _dest(tmp_path)
    dest.mkdir(parents=True)
    proc = run_bash(
        f'{SOURCE}; copy_maybe_launch_wizard "{kit}" "{dest}" root /root',
        env=_env({"COPY_FORCE_TTY": "1", "COPY_WIZARD_CMD": str(fake)}),
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not wiz_log.exists()
    assert "root で動いているため" in proc.stdout


def test_copy_failure_does_not_launch(tmp_path):
    fake, wiz_log = _fake_wizard(tmp_path)
    kit = _kit(tmp_path, payload=False, preflight=_pf(tmp_path, "exit 0"))
    proc = _run_main(tmp_path, kit,
                     extra={"COPY_FORCE_TTY": "1", "COPY_WIZARD_CMD": str(fake)})
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert not wiz_log.exists()
    assert not (tmp_path / "pf.log").exists()


def _launch_env(fake, **extra):
    return {"COPY_FORCE_TTY": "1", "COPY_WIZARD_CMD": str(fake), **extra}


def test_preflight_block_does_not_launch_and_explains(tmp_path):
    fake, wiz_log = _fake_wizard(tmp_path)
    kit = _kit(tmp_path, preflight=_pf(tmp_path, "echo '[BLOCK] docker: x'\nexit 2"))
    proc = _run_main(tmp_path, kit, extra=_launch_env(fake))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not wiz_log.exists()
    assert "[BLOCK] docker: x" in proc.stdout
    assert "BLOCK が出たため" in proc.stdout
    assert "解消してから" in proc.stdout


def test_preflight_crash_does_not_launch(tmp_path):
    fake, wiz_log = _fake_wizard(tmp_path)
    kit = _kit(tmp_path, preflight=_pf(tmp_path, "exit 7"))
    proc = _run_main(tmp_path, kit, extra=_launch_env(fake))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not wiz_log.exists()
    assert "戻り値 7" in proc.stdout


def test_preflight_warn_waits_for_enter_then_launches(tmp_path):
    fake, wiz_log = _fake_wizard(tmp_path)
    kit = _kit(tmp_path, preflight=_pf(tmp_path, "echo '[WARN] hostname: y'\nexit 0"))
    proc = _run_main(tmp_path, kit, extra=_launch_env(fake), stdin="\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Enter で初期設定を始めます" in proc.stdout + proc.stderr
    assert wiz_log.is_file()


def test_preflight_warn_eof_does_not_launch(tmp_path):
    fake, wiz_log = _fake_wizard(tmp_path)
    kit = _kit(tmp_path, preflight=_pf(tmp_path, "echo '[WARN] hostname: y'\nexit 0"))
    proc = _run_main(tmp_path, kit, extra=_launch_env(fake), stdin="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not wiz_log.exists()
    assert "入力を読めなかった" in proc.stdout


def test_preflight_ok_launches_without_prompt(tmp_path):
    fake, wiz_log = _fake_wizard(tmp_path)
    kit = _kit(tmp_path, preflight=_pf(
        tmp_path,
        "echo '[OK] repo: z'\necho '        [WARN] 字下げした行は数えない'\nexit 0"))
    proc = _run_main(tmp_path, kit, extra=_launch_env(fake), stdin="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert wiz_log.is_file()
    assert "Enter で初期設定を始めます" not in proc.stdout + proc.stderr


def test_missing_preflight_launches_with_notice(tmp_path):
    fake, wiz_log = _fake_wizard(tmp_path)
    proc = _run_main(tmp_path, _kit(tmp_path),
                     extra=_launch_env(fake, COPY_PREFLIGHT_CMD="none"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert wiz_log.is_file()
    assert "点検せずに初期設定を始めます" in proc.stdout


def test_launch_command_args_cwd_and_user(tmp_path):
    fake, wiz_log = _fake_wizard(tmp_path)
    kit = _kit(tmp_path, preflight=_pf(tmp_path, "exit 0"))
    proc = _run_main(tmp_path, kit, extra=_launch_env(fake))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = wiz_log.read_text(encoding="utf-8").splitlines()
    user = os.environ.get("USER") or pwd.getpwuid(os.getuid()).pw_name
    home = pwd.getpwnam(user).pw_dir
    # 引数なし: 先頭が PWD= の行（＝引数が 0 個）
    assert lines[0] == f"PWD={_dest(tmp_path)}"
    assert f"USER={user}" in lines
    assert f"HOME={home}" in lines
    assert "SUDO_USER=unset" in lines


def test_default_command_is_bash_repo_wizard(tmp_path):
    marker = tmp_path / "real-path-ran.log"
    kit = _kit(tmp_path, preflight=_pf(tmp_path, "exit 0"))
    wiz = kit / "payload" / "presence-logger" / "scripts" / "setup-hub-wizard.sh"
    wiz.write_text(f'echo "$0 $#" > "{marker}"\nexit 0\n', encoding="utf-8")
    proc = _run_main(tmp_path, kit, extra={"COPY_FORCE_TTY": "1"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert marker.read_text(encoding="utf-8").strip() == \
        f"{_dest(tmp_path) / 'scripts' / 'setup-hub-wizard.sh'} 0"


def test_wizard_failure_does_not_change_exit_code(tmp_path):
    fake, wiz_log = _fake_wizard(tmp_path)
    kit = _kit(tmp_path, preflight=_pf(tmp_path, "exit 0"))
    proc = _run_main(tmp_path, kit, extra=_launch_env(fake, FAKE_WIZ_RC="5"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert wiz_log.is_file()
    assert "完了していません（戻り値 5）" in proc.stdout


def test_preflight_runs_with_user_home(tmp_path):
    fake, _ = _fake_wizard(tmp_path)
    kit = _kit(tmp_path, preflight=_pf(tmp_path, 'echo "[OK] home: $HOME"\nexit 0'))
    proc = _run_main(tmp_path, kit, extra=_launch_env(fake))
    user = os.environ.get("USER") or pwd.getpwuid(os.getuid()).pw_name
    assert f"[OK] home: {pwd.getpwnam(user).pw_dir}" in proc.stdout


def test_preflight_prefers_kit_then_dest_scripts(tmp_path):
    fake, wiz_log = _fake_wizard(tmp_path)
    kit = _kit(tmp_path)
    (kit / "payload" / "presence-logger" / "scripts" / "preflight-new-hub.sh").write_text(
        "echo from-dest\nexit 0\n", encoding="utf-8")
    proc = _run_main(tmp_path, kit, extra=_launch_env(fake))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "from-dest" in proc.stdout
    assert wiz_log.is_file()
