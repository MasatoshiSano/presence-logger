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


# --- B2: 記録の送り先を付け替え先ハブの AP_GW_IP に揃える(設計 §2.4 (ii)) ----------------


def _send_target(root):
    return root / "home" / "pi" / "send_target_config.json"


def _dropin(root):
    return (
        root / "etc" / "systemd" / "system" / "child-csv-to-mqtt.service.d" / "10-hub-gw.conf"
    )


def _prepare_with_gw(root, tmp_path, gw, *, check=True):
    pub = tmp_path / "id_ed25519.pub"
    pub.write_text("ssh-ed25519 AAAA newhub\n", encoding="utf-8")
    return run_bash(
        f'{SOURCE}; child_sd_prepare "{root}" "{pub}" sibling-hub ap-secret9 "{gw}"',
        env=_env(),
        check=check,
    )


def test_prepare_aligns_json_host_and_writes_dropin_for_non_default_gw(tmp_path):
    """B2-T12: host だけが変わり、他のキー・所有者・モードは保たれる。"""
    import json
    import stat

    root = _child_root(tmp_path)
    original = {"host": "10.42.0.1", "password": "dummy-pass", "enabled": True, "port": 1883}
    cfg = _send_target(root)
    cfg.write_text(json.dumps(original), encoding="utf-8")
    cfg.chmod(0o640)
    before = cfg.stat()
    proc = _prepare_with_gw(root, tmp_path, "10.42.1.1")
    assert json.loads(cfg.read_text(encoding="utf-8")) == {**original, "host": "10.42.1.1"}
    after = cfg.stat()
    assert stat.S_IMODE(after.st_mode) == 0o640
    assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)
    assert _dropin(root).read_text(encoding="utf-8") == (
        "[Service]\nEnvironment=MQTT_HOST=10.42.1.1\n"
    )
    assert stat.S_IMODE(_dropin(root).stat().st_mode) == 0o644
    assert "10.42.0.1" in proc.stdout  # 「旧 → 新」を表示
    assert "10.42.1.1" in proc.stdout


def test_prepare_default_gw_resets_host_and_removes_existing_dropin(tmp_path):
    """B2-T13: 既定のハブへ戻す向き。"""
    import json

    root = _child_root(tmp_path)
    _send_target(root).write_text('{"host": "10.42.1.1", "password": "x"}', encoding="utf-8")
    _dropin(root).parent.mkdir(parents=True)
    _dropin(root).write_text("[Service]\nEnvironment=MQTT_HOST=10.42.1.1\n", encoding="utf-8")
    _prepare_with_gw(root, tmp_path, "10.42.0.1")
    assert json.loads(_send_target(root).read_text(encoding="utf-8")) == {
        "host": "10.42.0.1", "password": "x",
    }
    assert not _dropin(root).exists()


def test_prepare_default_gw_without_dropin_is_fine(tmp_path):
    root = _child_root(tmp_path)
    _prepare_with_gw(root, tmp_path, "10.42.0.1")
    assert not _dropin(root).exists()


def test_prepare_without_json_creates_only_host(tmp_path):
    """B2-T14: enabled を足さない(勝手に送信を始めない)。"""
    import json

    root = _child_root(tmp_path)
    assert not _send_target(root).exists()
    _prepare_with_gw(root, tmp_path, "10.42.1.1")
    assert json.loads(_send_target(root).read_text(encoding="utf-8")) == {"host": "10.42.1.1"}


def test_prepare_default_arg_is_default_gw(tmp_path):
    """既存の4引数呼び出しは既定(10.42.0.1)に揃える。"""
    import json

    root = _child_root(tmp_path)
    pub = tmp_path / "id_ed25519.pub"
    pub.write_text("ssh-ed25519 AAAA newhub\n", encoding="utf-8")
    run_bash(
        f'{SOURCE}; child_sd_prepare "{root}" "{pub}" sibling-hub ap-secret9',
        env=_env(),
    )
    assert json.loads(_send_target(root).read_text(encoding="utf-8")) == {"host": "10.42.0.1"}


def test_prepare_broken_json_is_not_overwritten_and_no_dropin(tmp_path):
    root = _child_root(tmp_path)
    _send_target(root).write_text("{broken", encoding="utf-8")
    proc = _prepare_with_gw(root, tmp_path, "10.42.1.1", check=False)
    assert proc.returncode != 0
    assert "壊れている" in proc.stderr
    assert _send_target(root).read_text(encoding="utf-8") == "{broken"
    assert not _dropin(root).exists()


def _snapshot(root):
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.mark.parametrize("broken", ["{broken", "[1, 2]"])
def test_prepare_broken_json_changes_nothing_on_the_sd(tmp_path, broken):
    """JSON の検査は何かを書く前。壊れていたら鍵・Wi-Fi・他 Wi-Fi・drop-in のどれも変えない。"""
    root = _child_root(tmp_path)
    conn = root / "etc" / "NetworkManager" / "system-connections"
    conn.mkdir(parents=True)
    (conn / "old-hub.nmconnection").write_text(
        "[connection]\nid=presence-hub\ntype=wifi\nautoconnect=true\n", encoding="utf-8"
    )
    _send_target(root).write_text(broken, encoding="utf-8")
    before = _snapshot(root)
    proc = _prepare_with_gw(root, tmp_path, "10.42.1.1", check=False)
    assert proc.returncode != 0
    assert "壊れている" in proc.stderr
    assert _snapshot(root) == before
    assert not _wifi_path(root).exists()
    assert not (root / "home" / "pi" / ".ssh" / "authorized_keys").exists()
    assert not _dropin(root).exists()


@pytest.mark.parametrize("bad", ["10.42.1", "10.42.1.256", "10.42.01.1", "10.42.1.1; rm", "x"])
def test_prepare_rejects_non_ipv4_gw_and_writes_nothing(tmp_path, bad):
    root = _child_root(tmp_path)
    proc = _prepare_with_gw(root, tmp_path, bad, check=False)
    assert proc.returncode != 0
    assert "AP_GW_IP" in proc.stderr
    assert not _send_target(root).exists()
    assert not _dropin(root).exists()


def test_align_send_target_keeps_non_ascii_values_intact(tmp_path):
    import json

    root = _child_root(tmp_path)
    _send_target(root).write_text(
        json.dumps({"host": "10.42.0.1", "note": "工場A"}, ensure_ascii=False), encoding="utf-8"
    )
    run_bash(f'{SOURCE}; child_sd_align_send_target "{root}" 10.42.1.1', env=_env())
    assert json.loads(_send_target(root).read_text(encoding="utf-8"))["note"] == "工場A"


def _site_env_repo(tmp_path, text):
    repo = tmp_path / "hub"
    repo.mkdir(exist_ok=True)
    if text is not None:
        (repo / "site.env").write_text(text, encoding="utf-8")
    return repo


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("AP_SSID=x\nAP_GW_IP=10.42.1.1\n", "10.42.1.1"),
        ('AP_GW_IP="10.42.2.1"\n', "10.42.2.1"),
        ("AP_SSID=x\n", "10.42.0.1"),
        ("AP_GW_IP=\n", "10.42.0.1"),
        (None, "10.42.0.1"),  # site.env が無い
    ],
)
def test_hub_gw_ip_reads_site_env_or_defaults(tmp_path, text, expected):
    """B2-T1(bash 側): python の load_ap_gw_ip と同じ表。"""
    repo = _site_env_repo(tmp_path, text)
    out = run_bash(f'{SOURCE}; child_sd_hub_gw_ip "{repo}"', env=_env()).stdout.strip()
    assert out == expected


@pytest.mark.parametrize("bad", ["10.42.1", "10.42.1.256", "10.42.01.1", "10.42.1.1/24", "abc"])
def test_hub_gw_ip_rejects_invalid(tmp_path, bad):
    repo = _site_env_repo(tmp_path, f"AP_GW_IP={bad}\n")
    proc = run_bash(f'{SOURCE}; child_sd_hub_gw_ip "{repo}"', env=_env(), check=False)
    assert proc.returncode != 0
    assert "AP_GW_IP" in proc.stderr


def test_hub_gw_ip_matches_python_loader(tmp_path):
    """bash と python の二重実装がずれていない。"""
    from fleet_ui.send_target import load_ap_gw_ip

    for text in ("AP_GW_IP=10.42.1.1\n", "AP_SSID=x\n", 'AP_GW_IP="10.42.9.1"\n'):
        repo = _site_env_repo(tmp_path, text)
        out = run_bash(f'{SOURCE}; child_sd_hub_gw_ip "{repo}"', env=_env()).stdout.strip()
        assert out == load_ap_gw_ip(repo)


def test_main_reads_site_env_gw_and_passes_it_to_the_sudo_reexec():
    """B2-T15: 値は1回だけ読み、sudo env の再実行へ引数で渡す(site.env を再読しない)。"""
    from pathlib import Path

    text = Path("scripts/prepare-child-sd.sh").read_text(encoding="utf-8")
    assert 'child_sd_hub_gw_ip "$PREPARE_REPO_DIR"' in text
    assert 'sudo env CHILD_SD_PUBKEY="$pub" HOME="$HOME"' in text
    assert '"$PREPARE_REPO_DIR/scripts/prepare-child-sd.sh" "$root" "$gw"' in text
    assert 'child_sd_prepare "$root" "$pub" "$ssid" "$psk" "$gw"' in text
