"""子のIPを子自身から取得することを検証する。

子のIPは親APのDHCP動的割当であり、インベントリや既定値に固定IPを持つと陳腐化する。
陳腐化したIPへのヘルスチェックは「別の子」に対して成功判定を出しうるため危険。
"""
import os

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/lib/deploy-common.sh"


def test_derives_ipv4_from_child(fake_bin):
    # hostname -I は "10.42.0.99 fe80::1" のように複数返す
    fake_bin("ssh", 'echo "10.42.0.99 fe80::1"')
    out = run_bash(
        f'{SOURCE}; CHILD_AP_IP=""; child_resolve_ap_ip; echo "RESULT=$CHILD_AP_IP"',
        env=dict(os.environ),
    ).stdout
    assert "RESULT=10.42.0.99" in out


def test_explicit_value_wins(fake_bin):
    fake_bin("ssh", 'echo "10.42.0.99"')
    out = run_bash(
        f'{SOURCE}; CHILD_AP_IP="10.42.0.7"; child_resolve_ap_ip; echo "RESULT=$CHILD_AP_IP"',
        env=dict(os.environ),
    ).stdout
    assert "RESULT=10.42.0.7" in out


def test_fails_loudly_when_ip_cannot_be_obtained(fake_bin):
    fake_bin("ssh", "exit 1")
    proc = run_bash(
        f'{SOURCE}; CHILD_AP_IP=""; child_resolve_ap_ip', env=dict(os.environ), check=False
    )
    assert proc.returncode != 0


def test_ignores_ipv6_only_output(fake_bin):
    fake_bin("ssh", 'echo "fe80::1 fd00::2"')
    proc = run_bash(
        f'{SOURCE}; CHILD_AP_IP=""; child_resolve_ap_ip', env=dict(os.environ), check=False
    )
    assert proc.returncode != 0
