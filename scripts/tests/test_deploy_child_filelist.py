"""deploy-child.sh が既定で機体固有設定を配らないことを検証する。

--dry-run は rsync までしか到達しないため、偽 rsync に渡された引数を見れば
「何を配ろうとしたか」を実際のコードパスで確認できる。
"""
import os

from scripts.tests.shellhelp import REPO_ROOT, run_bash

DEVICE_OWNED = [
    "id_names_config.json", "threshold_config.json", "recognition_config.json",
    "save_config.json", "model_config.json", "crop_config.json",
]


def _dry_run(fake_bin, args: str) -> str:
    fake_bin("rsync", 'printf "%s\\n" "$@" >> "$FAKE_LOG"')
    env = dict(os.environ)
    env["REPO_DIR"] = str(REPO_ROOT)
    run_bash(f"scripts/deploy-child.sh --dry-run {args}", env=env)
    return fake_bin.log.read_text(encoding="utf-8")


def test_default_does_not_distribute_device_owned_files(fake_bin):
    out = _dry_run(fake_bin, "")
    for name in DEVICE_OWNED:
        assert name not in out, f"{name} を既定で配布しようとしている"


def test_default_distributes_code_files(fake_bin):
    out = _dry_run(fake_bin, "")
    assert "Picamera.py" in out
    assert "web_server.py" in out
    assert "child-csv-to-mqtt.py" in out
    # child-csv-to-mqtt.py imports this; leaving it out breaks the child on deploy
    # (found 2026-09-25: this list wasn't updated when f03259d added the module).
    assert "ack_delivery.py" in out


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


# ---------------------------------------------------------------------------
# --with-shared-config は send_target_config.json の host をハブの AP_GW_IP に
# 書き換えてから配る(設計 §2.5)。host は「どのハブの AP にいるか」で決まる値。
# リポジトリの child/send_target_config.json は 10.42.0.1 を持つので、そのまま
# rsync すると非既定ハブの子の host が既定に戻り、送信が無警告で止まる。
# ---------------------------------------------------------------------------
import json  # noqa: E402
from pathlib import Path  # noqa: E402

_CHILD_CONFIG = REPO_ROOT / "child" / "send_target_config.json"


def _hub_repo(tmp_path: Path, site_env: str | None) -> Path:
    """child/ は本物へのリンク、site.env だけ差し替えた「ハブのリポジトリ」。"""
    repo = tmp_path / "hub"
    repo.mkdir()
    (repo / "child").symlink_to(REPO_ROOT / "child")
    if site_env is not None:
        (repo / "site.env").write_text(site_env, encoding="utf-8")
    return repo


def _rsync_capturing_config(fake_bin, tmp_path: Path) -> Path:
    """偽 rsync。渡された send_target_config.json の中身を、消える前に控える。"""
    captured = tmp_path / "captured-send-target.json"
    fake_bin(
        "rsync",
        'for a in "$@"; do\n'
        '  case "$a" in *send_target_config.json)\n'
        f'    [ -f "$a" ] && cp "$a" "{captured}" ;;\n'
        '  esac\n'
        'done',
    )
    return captured


def _dry_run_in(repo: Path, args: str):
    env = dict(os.environ)
    env["REPO_DIR"] = str(repo)
    return run_bash(f"scripts/deploy-child.sh --dry-run {args}", env=env, check=False)


def test_shared_config_is_rendered_with_the_hubs_ap_gw_ip(fake_bin, tmp_path):
    captured = _rsync_capturing_config(fake_bin, tmp_path)
    repo = _hub_repo(tmp_path, "AP_GW_IP=10.42.1.1\n")
    before = _CHILD_CONFIG.read_bytes()
    proc = _dry_run_in(repo, "--with-shared-config")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    sent = json.loads(captured.read_text(encoding="utf-8"))
    assert sent["host"] == "10.42.1.1"
    original = json.loads(before)
    assert {k: v for k, v in sent.items() if k != "host"} == \
        {k: v for k, v in original.items() if k != "host"}   # 他のキーは保たれる
    assert _CHILD_CONFIG.read_bytes() == before              # リポジトリ本体は変わらない


def test_shared_config_keeps_default_host_when_site_env_has_no_ap_gw_ip(fake_bin, tmp_path):
    captured = _rsync_capturing_config(fake_bin, tmp_path)
    repo = _hub_repo(tmp_path, "AP_SSID=presence-hub\n")
    assert _dry_run_in(repo, "--with-shared-config").returncode == 0
    assert json.loads(captured.read_text(encoding="utf-8"))["host"] == "10.42.0.1"


def test_shared_config_is_not_distributed_without_the_flag(fake_bin, tmp_path):
    captured = _rsync_capturing_config(fake_bin, tmp_path)
    repo = _hub_repo(tmp_path, "AP_GW_IP=10.42.1.1\n")
    assert _dry_run_in(repo, "").returncode == 0
    assert not captured.exists()
    assert "send_target_config.json" not in fake_bin.log.read_text(encoding="utf-8")


def test_invalid_ap_gw_ip_stops_the_shared_config_deploy(fake_bin, tmp_path):
    """壊れた値を子の設定へ配ってはいけない。配る前に止める。"""
    captured = _rsync_capturing_config(fake_bin, tmp_path)
    repo = _hub_repo(tmp_path, "AP_GW_IP=10.42.1.1; reboot\n")
    proc = _dry_run_in(repo, "--with-shared-config")
    assert proc.returncode != 0
    assert not captured.exists()


def _hub_ap_gw_ip(repo: Path):
    return run_bash(f'source scripts/lib/deploy-common.sh; hub_ap_gw_ip "{repo}"',
                    env=dict(os.environ), check=False)


def test_hub_ap_gw_ip_rule_table(tmp_path):
    """site.env に値あり → その値 / 無し → 既定 / 不正 → 非0。(B2 の load_ap_gw_ip と同じ表)"""
    def gw(body: str | None):
        d = tmp_path / f"c{abs(hash(body))}"
        d.mkdir()
        if body is not None:
            (d / "site.env").write_text(body, encoding="utf-8")
        return _hub_ap_gw_ip(d)

    p = gw("AP_GW_IP=10.42.1.1\n")
    assert (p.returncode, p.stdout.strip()) == (0, "10.42.1.1")
    p = gw('AP_GW_IP="10.42.2.1"   # 増設\n')
    assert (p.returncode, p.stdout.strip()) == (0, "10.42.2.1")
    p = gw("AP_SSID=x\n")
    assert (p.returncode, p.stdout.strip()) == (0, "10.42.0.1")
    p = gw(None)                                  # site.env 自体が無い
    assert (p.returncode, p.stdout.strip()) == (0, "10.42.0.1")
    for bad in ("AP_GW_IP=\n", "AP_GW_IP=10.42.1\n", "AP_GW_IP=10.42.1.256\n",
                "AP_GW_IP=10.42.01.1\n", "AP_GW_IP=10.42.1.1;x\n", "AP_GW_IP=abc\n"):
        p = gw(bad)
        assert p.returncode != 0, bad
        assert p.stdout.strip() == "", bad
