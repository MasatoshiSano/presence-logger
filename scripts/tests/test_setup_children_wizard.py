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
    assert "1 / 2" in proc.stderr


def test_kind_1_and_2_are_accepted():
    for n in "1", "2":
        proc = run_bash(
            f'{SOURCE}; children_wizard_validate_kind {n}',
            env=_env(),
            check=False,
        )
        assert proc.returncode == 0, proc.stderr


def test_kind_other_is_rejected():
    proc = run_bash(
        f'{SOURCE}; children_wizard_validate_kind 9',
        env=_env(),
        check=False,
    )
    assert proc.returncode != 0
    assert "1 / 2" in proc.stderr


def test_json_message_fails_when_not_ok():
    proc = run_bash(
        f'{SOURCE}; printf \'%s\\n\' \'{{"ok": false, "message": "公開鍵を入れられませんでした"}}\' | children_json_message',
        env=_env(),
        check=False,
    )
    assert proc.returncode != 0
    assert "公開鍵" in proc.stderr


def test_json_message_prints_success():
    proc = run_bash(
        f'{SOURCE}; printf \'%s\\n\' \'{{"ok": true, "message": "移しました"}}\' | children_json_message',
        env=_env(),
    )
    assert "移しました" in proc.stdout


def test_sd_root_rejects_relative_and_injection():
    proc = run_bash(
        f'{SOURCE}; children_wizard_validate_sd_root "media/rootfs"',
        env=_env(),
        check=False,
    )
    assert proc.returncode != 0
    proc = run_bash(
        f'{SOURCE}; children_wizard_validate_sd_root "/tmp/root; rm -rf /"',
        env=_env(),
        check=False,
    )
    assert proc.returncode != 0


def test_sd_root_accepts_absolute_path():
    proc = run_bash(
        f'{SOURCE}; children_wizard_validate_sd_root /media/pi/rootfs',
        env=_env(),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_wizard_does_not_embed_psk_in_bash_c():
    from pathlib import Path
    text = Path("scripts/setup-children-wizard.sh").read_text(encoding="utf-8")
    assert "sudo bash -c" not in text
    write_sd = text.split("children_wizard_write_sd()")[1].split("children_wizard_adopt_on_ap()")[0]
    assert "CHILD_SD_PUBKEY" in write_sd
    assert "psk=" not in write_sd
    assert "sudo env CHILD_SD_PUBKEY=" in write_sd
    assert 'children_ask "マウント先' not in text
    assert "children_wizard_wait_for_child_sd" in write_sd


def test_new_child_default_hostname_is_hub_plus_ordinal():
    from pathlib import Path
    text = Path("scripts/setup-children-wizard.sh").read_text(encoding="utf-8")
    assert "このハブのホスト名-001" in text
    assert "children_cli suggest" in text


def test_find_sd_root_prefers_media_then_mnt(tmp_path):
    media = tmp_path / "media" / "pi" / "child"
    (media / "home" / "pi").mkdir(parents=True)
    (media / "home" / "pi" / "id_names_config.json").write_text("{}\n", encoding="utf-8")
    proc = run_bash(
        f'{SOURCE}; child_sd_find_root "{tmp_path}/media"',
        env=_env(),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert str(media) in proc.stdout
