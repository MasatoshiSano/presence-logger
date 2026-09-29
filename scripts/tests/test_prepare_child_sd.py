"""クローンした子SDへ、このハブの鍵と AP を書く処理の検証。

旧親が居ない／別工場だと入れ子 SSH は使えない。ホスト名と局番号は残す。
"""
import os

import pytest

from scripts.tests import nm_vectors
from scripts.tests.nm_readback import (
    assert_glib_roundtrip,
    assert_nm_roundtrip,
    assert_roundtrip,
    nm_read,
)
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


def _wifi_path(root):
    return (
        root / "etc" / "NetworkManager" / "system-connections" / "presence-hub-join.nmconnection"
    )


def _write_wifi(root, ssid, psk, *, check=True):
    """値は環境変数で渡す(TAB・引用符・末尾空白をシェルの引用に頼らず届ける)。"""
    return run_bash(
        f'{SOURCE}; child_sd_write_wifi "{root}" "$T_SSID" "$T_PSK"',
        env=_env({"T_SSID": ssid, "T_PSK": psk}),
        check=check,
    )


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
    wifi = (
        root / "etc" / "NetworkManager" / "system-connections"
        / "presence-hub-join.nmconnection"
    )
    # A-R1: 文字列一致ではなく、NM と GLib の読み手で元の値に読み戻せること。
    assert_roundtrip(wifi, "sibling-hub", "ap-secret9")
    assert nm_read(wifi)["ssid"] == "sibling-hub"
    assert "autoconnect-priority=200" in wifi.read_text(encoding="utf-8")
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


@pytest.mark.parametrize(("ssid", "psk"), nm_vectors.OK)
def test_prepare_psk_with_hash_reads_back_intact(tmp_path, ssid, psk):
    """A-R2/R4/R5: `#` `;` `\\` `"` 先頭空白・末尾空白・非ASCII SSID・16進64桁。

    引用符で囲むと引用符ごと値になる(2026-09-25)。未引用でも `#` はコメントにならない。"""
    root = _child_root(tmp_path)
    _write_wifi(root, ssid, psk)
    assert_roundtrip(_wifi_path(root), ssid, psk)


@pytest.mark.parametrize(("ssid", "psk"), nm_vectors.OK)
def test_prepare_ssid_psk_lines_are_not_quoted(tmp_path, ssid, psk):
    """A-R3: 引用符で囲まない(再発防止。読み戻しの補助)。"""
    root = _child_root(tmp_path)
    _write_wifi(root, ssid, psk)
    lines = _wifi_path(root).read_text(encoding="utf-8").splitlines()
    for key in ("ssid=", "psk="):
        (line,) = [ln for ln in lines if ln.startswith(key)]
        if not (ssid.startswith('"') or psk.startswith('"')):
            assert not line[len(key):].startswith('"'), line


def test_prepare_writes_bytes_form_for_non_ascii_ssid(tmp_path):
    """A-R4: 非ASCII は NM 自身と同じバイト列形式。"""
    root = _child_root(tmp_path)
    _write_wifi(root, "工場-hub", "plain-pass-1")
    body = _wifi_path(root).read_text(encoding="utf-8")
    assert "ssid=229;183;165;229;160;180;45;104;117;98;\n" in body
    assert_nm_roundtrip(_wifi_path(root), "工場-hub", "plain-pass-1")


def test_prepare_semicolon_in_ssid_is_escaped_like_nm(tmp_path):
    """A-R5: セミコロンは二重バックスラッシュ付きで書く(NM と同じ)。

    片方だけだと NM が SSID ごと落とす。"""
    root = _child_root(tmp_path)
    _write_wifi(root, "a;b", "plain-pass-1")
    body = _wifi_path(root).read_text(encoding="utf-8")
    assert "ssid=a\\\\;b\n" in body
    assert_roundtrip(_wifi_path(root), "a;b", "plain-pass-1")


def test_nm_reads_hand_written_bytes_form_ssid(tmp_path):
    """A-R6: ヘルパ自体の前提。手書きのバイト列形式を NM が `trail ` として読む。"""
    f = tmp_path / "hand.nmconnection"
    f.write_text(
        "[connection]\nid=presence-hub-join\ntype=wifi\n\n"
        "[wifi]\nmode=infrastructure\nssid=116;114;97;105;108;32;\n\n"
        "[wifi-security]\nkey-mgmt=wpa-psk\npsk=plain-pass-1\n",
        encoding="utf-8",
    )
    assert nm_read(f)["ssid"] == "trail "
    assert_glib_roundtrip(f, "trail ", "plain-pass-1")


@pytest.mark.parametrize("bad", nm_vectors.BAD_PSK)
def test_prepare_rejects_psk_wpa_forbids(tmp_path, bad):
    """A-R7: 書く前に拒否。前のファイルを壊さない。エラー文に PSK を含めない。"""
    root = _child_root(tmp_path)
    _write_wifi(root, "sibling-hub", "ap-secret9")
    before = _wifi_path(root).read_bytes()
    proc = _write_wifi(root, "sibling-hub", bad, check=False)
    assert proc.returncode != 0
    assert _wifi_path(root).read_bytes() == before
    assert bad not in proc.stdout + proc.stderr


def test_prepare_bad_psk_creates_no_file(tmp_path):
    """A-R7: 前のファイルが無いときは、ファイルを作らない。"""
    root = _child_root(tmp_path)
    proc = _write_wifi(root, "sibling-hub", "short12", check=False)
    assert proc.returncode != 0
    assert not _wifi_path(root).exists()


@pytest.mark.parametrize("bad", nm_vectors.BAD_SSID)
def test_prepare_rejects_ssid_outside_1_to_32_bytes(tmp_path, bad):
    """A-R8: 空、33 バイト以上(非ASCII はバイト数で数える)。"""
    root = _child_root(tmp_path)
    if bad == "":
        # ssid="${2:?}" が空を止めるので、関数へは空文字を明示的に渡す形で確認する
        proc = run_bash(
            f'{SOURCE}; child_sd_write_wifi "{root}" "" "plain-pass-1"',
            env=_env(),
            check=False,
        )
    else:
        proc = _write_wifi(root, bad, "plain-pass-1", check=False)
    assert proc.returncode != 0
    assert not _wifi_path(root).exists()


def test_prepare_reports_and_repairs_legacy_quoted_profile(tmp_path):
    """A-R9: 旧書式(引用符つき)の SD は上書きで直り、直したことを表示する。"""
    root = _child_root(tmp_path)
    wifi = _wifi_path(root)
    wifi.parent.mkdir(parents=True)
    wifi.write_text(
        "[connection]\nid=presence-hub-join\ntype=wifi\n\n"
        '[wifi]\nmode=infrastructure\nssid="sibling-hub"\n\n'
        '[wifi-security]\nkey-mgmt=wpa-psk\npsk="ap-secret9"\n',
        encoding="utf-8",
    )
    proc = _write_wifi(root, "sibling-hub", "ap-secret9")
    assert "以前の書式（引用符つき）" in proc.stdout
    assert_roundtrip(wifi, "sibling-hub", "ap-secret9")


def test_prepare_says_nothing_about_legacy_when_profile_is_clean(tmp_path):
    root = _child_root(tmp_path)
    _write_wifi(root, "sibling-hub", "ap-secret9")
    proc = _write_wifi(root, "sibling-hub", "ap-secret9")  # 2回目: 新書式が既にある
    assert "以前の書式" not in proc.stdout
    fresh = _write_wifi(_child_root(tmp_path / "other"), "sibling-hub", "ap-secret9")
    assert "以前の書式" not in fresh.stdout


def test_sudo_reexec_passes_pubkey_and_home():
    from pathlib import Path
    text = Path("scripts/prepare-child-sd.sh").read_text(encoding="utf-8")
    assert 'sudo env CHILD_SD_PUBKEY="$pub" HOME="$HOME"' in text
    assert 'sudo bash "$PREPARE_REPO_DIR/scripts/prepare-child-sd.sh" "$root"' not in text
    assert "トップの番号 3 はありません" in text
    assert "1) すでに動いている子を移す" in text


def test_prepare_disables_old_wifi_autoconnect(tmp_path):
    root = _child_root(tmp_path)
    conn = root / "etc" / "NetworkManager" / "system-connections"
    conn.mkdir(parents=True)
    old = conn / "old-hub.nmconnection"
    old.write_text(
        "[connection]\nid=presence-hub\ntype=wifi\nautoconnect=true\n",
        encoding="utf-8",
    )
    pub = tmp_path / "id_ed25519.pub"
    pub.write_text("ssh-ed25519 AAAA newhub\n", encoding="utf-8")
    run_bash(
        f'{SOURCE}; child_sd_prepare "{root}" "{pub}" sibling-hub ap-secret9',
        env=_env(),
    )
    body = old.read_text(encoding="utf-8")
    assert "autoconnect=false" in body
    assert "autoconnect=true" not in body


def test_sd_wifi_profile_never_gives_up_reconnecting(tmp_path):
    """NetworkManager の既定は4回で自動接続を諦める。子には無限再試行が要る。

    2026-09-23 の AP パスワード切替で、既定のまま4回失敗した子2台が
    自動接続を放棄し、電波が正しく戻っても再試行しなくなった。復旧には
    物理的な電源再投入が必要だった。SD に書くプロファイルも同じ穴を持つ。
    """
    root = _child_root(tmp_path)
    _write_wifi(root, "sibling-hub", "ap-secret9")
    # A-R10: NM 自身の読み手が autoconnect-retries=0 として読むこと。
    from scripts.tests.nm_readback import _nmcli, _pick

    proc = _nmcli(
        ["connection", "modify", "connection.autoconnect-priority", "200"],
        stdin=_wifi_path(root).read_text(encoding="utf-8"),
    )
    assert _pick(proc.stdout)["autoconnect-retries"] == "0", "4回で諦めると電源再投入が要る"
