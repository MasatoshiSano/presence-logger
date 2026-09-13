"""サービスが「そもそも起動しない」ときも自動ロールバックが走ることの検証。

以前は child_restart_units が restart 失敗時に die (= exit 1) していたため、
deploy-child.sh のロールバック処理へ到達しなかった。実機で確認したところ、
子は failed のまま放置され、バックアップは作られたのに復元されなかった。

つまり自動ロールバックが「起動はしたが不健全」しかカバーせず、
「そもそも起動しない」を取りこぼしていた。不良リリースで service が起動しない場合、
新コードが載ったまま子が壊れて残る。
"""
import os

from scripts.tests.shellhelp import REPO_ROOT, run_bash

SOURCE = "source scripts/lib/deploy-common.sh"


def _ssh_ok_but_restart_fails(fake_bin):
    """restart だけ失敗し、他は成功する偽 ssh。"""
    fake_bin("ssh", """
cmd="$*"
case "$cmd" in
  *"systemctl restart"*) echo "Job for x.service failed" >&2; exit 1 ;;
  *hostname\\ -I*)        echo "10.42.0.52" ;;
  *df\\ -Pm*)             echo "20000" ;;
  *is-active*)           echo "active" ;;
  *)                     echo "" ;;
esac
exit 0
""")


def test_restart_units_returns_nonzero_instead_of_exiting(fake_bin):
    """die せず戻り値で失敗を伝えること。呼び出し側が rollback へ分岐できる。

    `set -e` 下では `f; echo $?` と書くと f が非0を返した時点でシェルが終了し
    echo に届かない。実際の呼ばれ方と同じく if の条件として評価する。
    """
    _ssh_ok_but_restart_fails(fake_bin)
    proc = run_bash(
        f'{SOURCE}; if child_restart_units picamera; then echo "RC=0"; else echo "RC=1"; fi',
        env=dict(os.environ), check=False,
    )
    assert "RC=1" in proc.stdout, proc.stdout + proc.stderr


def test_restart_units_returns_zero_on_success(fake_bin):
    fake_bin("ssh", 'exit 0')
    proc = run_bash(
        f'{SOURCE}; if child_restart_units picamera; then echo "RC=0"; else echo "RC=1"; fi',
        env=dict(os.environ), check=False,
    )
    assert "RC=0" in proc.stdout, proc.stdout + proc.stderr


def test_deploy_child_attempts_rollback_when_restart_fails(fake_bin, tmp_path):
    """統合: restart が失敗したら復元処理まで到達すること。

    以前はここで die して終わり、'ロールバック' の文字すら出なかった。
    """
    _ssh_ok_but_restart_fails(fake_bin)
    fake_bin("rsync", "exit 0")
    fake_bin("curl", "exit 7")  # web_server も応答しない = 不健全
    env = dict(os.environ)
    env["HEALTH_STABLE_WAIT"] = "0"
    env["MODEL_READY_WAIT"] = "0"
    env["REPO_DIR"] = str(REPO_ROOT)
    proc = run_bash("scripts/deploy-child.sh", env=env, check=False)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "ロールバック" in out, out


def test_deploy_child_still_succeeds_when_everything_healthy(fake_bin):
    """退行防止: 正常時は今までどおり成功すること。"""
    fake_bin("ssh", """
cmd="$*"
case "$cmd" in
  *hostname\\ -I*) echo "10.42.0.52" ;;
  *df\\ -Pm*)      echo "20000" ;;
  *)              echo "" ;;
esac
exit 0
""")
    fake_bin("rsync", "exit 0")
    fake_bin("curl", 'echo "{\\"status\\": \\"ready\\"}"')
    env = dict(os.environ)
    env["HEALTH_STABLE_WAIT"] = "0"
    env["REPO_DIR"] = str(REPO_ROOT)
    proc = run_bash("scripts/deploy-child.sh", env=env, check=False)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "デプロイ成功" in out, out
