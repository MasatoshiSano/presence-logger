"""子付け替えの対話ウィザードの検証。"""
import os

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/setup-children-wizard.sh"


def _env(extra=None):
    env = dict(os.environ)
    if extra:
        env.update(extra)
    return env


def test_mode_1_2_3_are_accepted():
    for n in "1", "2", "3":
        proc = run_bash(
            f'{SOURCE}; children_wizard_validate_mode {n}',
            env=_env(),
            check=False,
        )
        assert proc.returncode == 0, proc.stderr


def test_mode_other_is_rejected():
    proc = run_bash(
        f'{SOURCE}; children_wizard_validate_mode 9',
        env=_env(),
        check=False,
    )
    assert proc.returncode != 0
    assert "1 / 2 / 3" in proc.stderr


def test_old_host_injection_is_rejected():
    proc = run_bash(
        f'{SOURCE}; children_wizard_validate_old_host "host; rm"',
        env=_env(),
        check=False,
    )
    assert proc.returncode != 0


def test_old_host_ipv4_is_accepted():
    proc = run_bash(
        f'{SOURCE}; children_wizard_validate_old_host 172.22.13.17',
        env=_env(),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_json_entries_helper():
    proc = run_bash(
        f'{SOURCE}; printf \'%s\\n\' \'{{"ok": true, "children": [{{"entry": "zero2"}}, {{"entry": "other.local"}}]}}\' | children_json_entries',
        env=_env(),
    )
    assert proc.stdout.split() == ["zero2", "other.local"]


def test_wizard_stops_on_eof_instead_of_looping():
    proc = run_bash(
        "timeout 8 bash -c 'source scripts/setup-children-wizard.sh; main'",
        env=_env(),
        check=False,
        stdin="9\n",
    )
    assert proc.returncode != 0
    assert "1 / 2 / 3" in proc.stderr
