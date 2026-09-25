"""ACK 往復検証 (verify-ack-roundtrip.sh) の判定ロジック。

「Oracle に入った」と「子がそれを知った」は別物。前者だけ見て成功と呼ぶと、
2026-09-23 に見つけた穴(failed 897件を子が送信済みとして持っていた)を
また見逃す。判定はこの2つを必ず分けて出す。
"""
import os

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/verify-ack-roundtrip.sh"


def _env(extra=None):
    env = dict(os.environ)
    if extra:
        env.update(extra)
    return env


def test_commit_without_child_ack_is_not_a_pass():
    """Oracle に入っても子が知らなければ、検証は通していない。"""
    out = run_bash(
        f'{SOURCE}; ack_verdict committed=3 acks_published=3 child_acked=0',
        env=_env(), check=False,
    ).stdout
    assert "PASS" not in out
    assert "子" in out


def test_all_three_layers_agree_is_a_pass():
    out = run_bash(
        f'{SOURCE}; ack_verdict committed=3 acks_published=3 child_acked=3',
        env=_env(), check=False,
    ).stdout
    assert "PASS" in out


def test_ack_published_but_none_committed_is_a_contradiction():
    """コミットしていないのに ACK が出ているなら、嘘の ACK を出している。"""
    out = run_bash(
        f'{SOURCE}; ack_verdict committed=0 acks_published=2 child_acked=2',
        env=_env(), check=False,
    ).stdout
    assert "PASS" not in out
    assert "矛盾" in out


def test_nothing_happened_is_reported_as_inconclusive():
    """1件も流れなければ、合格でも不合格でもない。"""
    out = run_bash(
        f'{SOURCE}; ack_verdict committed=0 acks_published=0 child_acked=0',
        env=_env(), check=False,
    ).stdout
    assert "PASS" not in out
    assert "判定できません" in out
