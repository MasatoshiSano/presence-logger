"""クローンした子SDへ、このハブの鍵と AP を書く処理の検証。

旧親が居ない／別工場だと入れ子 SSH は使えない。ホスト名と局番号は残す。
"""
import os

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/prepare-child-sd.sh"


def _env(extra=None):
    env = dict(os.environ)
    if extra:
        env.update(extra)
    return env


def _child_root(tmp_path):
    root = tmp_path / "rootfs"
    home = root / "home" / "pi"
    home.mkdir(parents=True)
    (home / "id_names_config.json").write_text(
        '{"id_names":{"1":["H","T","1"]}}\n', encoding="utf-8"
    )
    (home / "hostname-keep").write_text("zero2\n", encoding="utf-8")
    return root


def test_find_root_from_media_tree(tmp_path):
    media = tmp_path / "media" / "usb"
    root = media / "rootfs"
    (root / "home" / "pi").mkdir(parents=True)
    (root / "home" / "pi" / "id_names_config.json").write_text("{}\n", encoding="utf-8")
    found = run_bash(
        f'{SOURCE}; child_sd_find_root "{tmp_path / "media"}"',
        env=_env(),
    ).stdout.strip()
    assert found == str(root)


def test_hub_sd_is_rejected(tmp_path):
    root = _child_root(tmp_path)
    etc = root / "etc" / "presence-logger"
    etc.mkdir(parents=True)
    (etc / "profiles.yaml").write_text("profiles: {}\n", encoding="utf-8")
    proc = run_bash(
        f'{SOURCE}; child_sd_is_child_root "{root}"',
        env=_env(),
        check=False,
    )
    assert proc.returncode != 0
    assert "ハブ" in proc.stderr


def test_prepare_writes_key_and_wifi_keeps_identity(tmp_path):
    root = _child_root(tmp_path)
    pub = tmp_path / "id_ed25519.pub"
    pub.write_text("ssh-ed25519 AAAA newhub\n", encoding="utf-8")
    run_bash(
        f'{SOURCE}; child_sd_prepare "{root}" "{pub}" sibling-hub ap-secret9',
        env=_env(),
    )
    keys = (root / "home" / "pi" / ".ssh" / "authorized_keys").read_text(encoding="utf-8")
    assert "ssh-ed25519 AAAA newhub" in keys
    wifi = (root / "etc" / "NetworkManager" / "system-connections" / "presence-hub-join.nmconnection")
    body = wifi.read_text(encoding="utf-8")
    assert 'ssid="sibling-hub"' in body
    assert 'psk="ap-secret9"' in body
    assert "autoconnect-priority=200" in body
    assert oct(wifi.stat().st_mode & 0o777) == "0o600"
    assert (root / "home" / "pi" / "id_names_config.json").read_text(encoding="utf-8") == (
        '{"id_names":{"1":["H","T","1"]}}\n'
    )
    assert not (root / "etc" / "hostname").exists()


def test_prepare_does_not_duplicate_pubkey(tmp_path):
    root = _child_root(tmp_path)
    pub = tmp_path / "id_ed25519.pub"
    pub.write_text("ssh-ed25519 AAAA newhub\n", encoding="utf-8")
    run_bash(
        f'{SOURCE}; child_sd_install_pubkey "{root}" "{pub}"; '
        f'child_sd_install_pubkey "{root}" "{pub}"',
        env=_env(),
    )
    keys = (root / "home" / "pi" / ".ssh" / "authorized_keys").read_text(encoding="utf-8")
    assert keys.count("ssh-ed25519 AAAA newhub") == 1


def test_prepare_quotes_hash_in_psk_so_nm_does_not_truncate(tmp_path):
    root = _child_root(tmp_path)
    pub = tmp_path / "id_ed25519.pub"
    pub.write_text("ssh-ed25519 AAAA newhub\n", encoding="utf-8")
    run_bash(
        f'{SOURCE}; child_sd_write_wifi "{root}" sibling-hub "sec#ret99"',
        env=_env(),
    )
    body = (
        root / "etc" / "NetworkManager" / "system-connections" / "presence-hub-join.nmconnection"
    ).read_text(encoding="utf-8")
    assert 'psk="sec#ret99"' in body


def test_sudo_reexec_passes_pubkey_and_home():
    from pathlib import Path
    text = Path("scripts/prepare-child-sd.sh").read_text(encoding="utf-8")
    assert 'sudo env CHILD_SD_PUBKEY="$pub" HOME="$HOME"' in text
    assert 'sudo bash "$PREPARE_REPO_DIR/scripts/prepare-child-sd.sh" "$root"' not in text
