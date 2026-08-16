"""deploy-child.sh が既定で機体固有設定を配らないことを検証する。

--dry-run は rsync までしか到達しないため、偽 rsync に渡された引数を見れば
「何を配ろうとしたか」を実際のコードパスで確認できる。
"""
import os

from scripts.tests.shellhelp import run_bash

DEVICE_OWNED = [
    "id_names_config.json", "threshold_config.json", "recognition_config.json",
    "save_config.json", "model_config.json", "crop_config.json",
]


def _dry_run(fake_bin, args: str) -> str:
    fake_bin("rsync", 'printf "%s\\n" "$@" >> "$FAKE_LOG"')
    run_bash(f"scripts/deploy-child.sh --dry-run {args}", env=dict(os.environ))
    return fake_bin.log.read_text(encoding="utf-8")


def test_default_does_not_distribute_device_owned_files(fake_bin):
    out = _dry_run(fake_bin, "")
    for name in DEVICE_OWNED:
        assert name not in out, f"{name} を既定で配布しようとしている"


def test_default_distributes_code_files(fake_bin):
    out = _dry_run(fake_bin, "")
    assert "Picamera.py" in out
    assert "web_server.py" in out


def test_default_does_not_distribute_shared_config(fake_bin):
    out = _dry_run(fake_bin, "")
    assert "status_code_config.json" not in out


def test_with_shared_config_distributes_shared_only(fake_bin):
    out = _dry_run(fake_bin, "--with-shared-config")
    assert "status_code_config.json" in out
    assert "send_target_config.json" in out
    for name in DEVICE_OWNED:
        assert name not in out, f"--with-shared-config で {name} を配布しようとしている"


def test_code_only_is_accepted_as_deprecated_alias(fake_bin):
    """既存手順を壊さないため、--code-only は受理して既定と同じ動作にする。"""
    out = _dry_run(fake_bin, "--code-only")
    assert "Picamera.py" in out
    for name in DEVICE_OWNED:
        assert name not in out
