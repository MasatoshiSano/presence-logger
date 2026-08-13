"""期待モデルの照合が /model_status の応答だけで完結することの検証。

背景: 以前は照合先が /current_model だった。しかし実機(zero2)の /current_model は
`{"network": ..., "labels": ...}` しか返さず model_type を持たないため、正常に
signal_tower で動いている子に対しても「有効モデルが期待と不一致」となり、
deploy-model.sh が毎回 readiness ゲートで落ちて自動ロールバックしていた。
/model_status には status と model_type の両方が入っているので、そこから読む。
"""
import os

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/lib/deploy-common.sh"
PRELUDE = f'{SOURCE}; CHILD_AP_IP=10.0.0.1; MODEL_READY_WAIT=2'


def _curl(fake_bin, status_body: str, current_model_body: str = "CALLED", rc_cm: int = 1):
    """URL で応答を出し分ける偽 curl。

    既定では /current_model を呼ぶと失敗する。もし実装が /current_model を
    見に行っていたら、そのせいでテストが落ちる — 退行検知になる。
    """
    fake_bin("curl", f"""
for a in "$@"; do
  case "$a" in
    */model_status)  printf '%s' '{status_body}'; exit 0 ;;
    */current_model) printf '%s' '{current_model_body}'; exit {rc_cm} ;;
  esac
done
exit 1
""")


def _run(expect: str = "") -> int:
    return run_bash(
        f'{PRELUDE}; child_model_ready {expect}', env=dict(os.environ), check=False
    ).returncode


def test_matching_model_passes_without_touching_current_model(fake_bin):
    _curl(fake_bin, '{"status": "ready", "model_type": "signal_tower"}')
    assert _run("signal_tower") == 0


def test_mismatched_model_fails(fake_bin):
    _curl(fake_bin, '{"status": "ready", "model_type": "object_detection"}')
    assert _run("signal_tower") == 1


def test_not_ready_fails(fake_bin):
    _curl(fake_bin, '{"status": "switching", "model_type": "signal_tower"}')
    assert _run("signal_tower") == 1


def test_no_expected_model_only_checks_readiness(fake_bin):
    """deploy-child.sh は引数なしで呼ぶ。model_type を見ないこと。"""
    _curl(fake_bin, '{"status": "ready"}')
    assert _run() == 0


def test_current_model_without_model_type_does_not_break_match(fake_bin):
    """実機 zero2 の形。/current_model に model_type が無くても照合は通る。"""
    _curl(
        fake_bin,
        '{"status": "ready", "model_type": "signal_tower"}',
        current_model_body='{"network": "/home/pi/signal_tower/network.rpk"}',
        rc_cm=0,
    )
    assert _run("signal_tower") == 0
