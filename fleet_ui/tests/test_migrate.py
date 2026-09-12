"""既存の子を旧ハブからこのハブへ付け替える処理の検証。

新機登録ウィザード(STA_NO を空にする / 改名)を流すと、稼働中の子の記録が止まる。
引っ越しはホスト名と局番号を残し、WiFi と SSH 鍵とインベントリだけを付け替える。
"""
from pathlib import Path

from fleet_ui.migrate import (
    CAT_OK,
    KEY_OK,
    inventory_name,
    list_children_payload,
    list_remote_children,
    load_ap_join,
    migrate_status,
    parse_children_conf,
    read_pubkey,
    strip_inventory_entry,
    take_child,
    validate_old_host,
)


def test_parse_children_conf_skips_comments_and_blank_lines():
    text = "# header\nzero2\n\npizero2w-2.local  # note\n"
    assert parse_children_conf(text) == ["zero2", "pizero2w-2.local"]


def test_strip_inventory_entry_keeps_comments_and_other_hosts():
    text = "# header\nzero2\npizero2w-2.local\n"
    out = strip_inventory_entry(text, "zero2")
    assert "zero2" not in [ln.split("#", 1)[0].strip() for ln in out.splitlines() if ln.strip()]
    assert "pizero2w-2.local" in out
    assert "# header" in out


def test_inventory_name_adds_local_suffix_when_missing():
    assert inventory_name("zero2") == "zero2.local"
    assert inventory_name("pizero2w-2.local") == "pizero2w-2.local"


def test_validate_old_host_accepts_ipv4_and_mdns():
    assert validate_old_host("172.22.13.17") is None
    assert validate_old_host("raspberrypi5.local") is None


def test_validate_old_host_rejects_injection():
    assert validate_old_host("-oProxyCommand=x") is not None
    assert validate_old_host("host; rm -rf /") is not None
    assert validate_old_host("pi@172.22.13.17") is not None
    assert validate_old_host("") is not None


def test_load_ap_join_reads_kit_file_not_oracle_password(tmp_path):
    kit = tmp_path / ".kit"
    kit.mkdir()
    (kit / "ap-join.env").write_text(
        "AP_SSID=sibling-hub\nWIFI_AP_PSK=secret-ap-9\n", encoding="utf-8"
    )
    (tmp_path / "site.env").write_text("AP_SSID=from-site\n", encoding="utf-8")
    ssid, psk = load_ap_join(tmp_path)
    assert ssid == "sibling-hub"
    assert psk == "secret-ap-9"


def test_load_ap_join_falls_back_to_site_env_and_kit_secrets(tmp_path):
    kit = tmp_path / ".kit"
    kit.mkdir()
    (kit / "secrets.env").write_text(
        "ORACLE_PASSWORD_HHC=nope\nWIFI_AP_PSK=from-secrets\n", encoding="utf-8"
    )
    (tmp_path / "site.env").write_text("AP_SSID=presence-hub-2\n", encoding="utf-8")
    ssid, psk = load_ap_join(tmp_path)
    assert ssid == "presence-hub-2"
    assert psk == "from-secrets"


def test_load_ap_join_keeps_hash_inside_psk(tmp_path):
    kit = tmp_path / ".kit"
    kit.mkdir()
    (kit / "ap-join.env").write_text(
        "AP_SSID=sibling-hub\nWIFI_AP_PSK=sec#ret99\n", encoding="utf-8"
    )
    ssid, psk = load_ap_join(tmp_path)
    assert ssid == "sibling-hub"
    assert psk == "sec#ret99"


def test_migrate_status_never_returns_the_psk(tmp_path):
    kit = tmp_path / ".kit"
    kit.mkdir()
    (kit / "ap-join.env").write_text(
        "AP_SSID=sibling-hub\nWIFI_AP_PSK=secret-ap-9\n", encoding="utf-8"
    )
    pub = tmp_path / "id_ed25519.pub"
    pub.write_text("ssh-ed25519 AAAA comment\n", encoding="utf-8")
    st = migrate_status(repo=tmp_path, pubkey_path=pub)
    assert set(st) == {"pubkey", "ap_ssid", "has_psk"}
    assert st["ap_ssid"] == "sibling-hub"
    assert st["has_psk"] is True
    assert st["pubkey"] == "ssh-ed25519 AAAA comment"
    assert "secret-ap-9" not in str(st)


def test_read_pubkey_returns_stripped_line(tmp_path):
    p = tmp_path / "id_ed25519.pub"
    p.write_text("ssh-ed25519 AAAA comment\n", encoding="utf-8")
    assert read_pubkey(p) == "ssh-ed25519 AAAA comment"


def _recorder(script):
    """ssh の remote 文字列を見て、stdout を返す偽 runner。"""
    calls = []

    def run(cmd):
        calls.append(cmd)
        remote = cmd[-1] if cmd else ""
        joined = " ".join(cmd)
        return script(joined, remote)

    run.calls = calls
    return run


def test_take_child_installs_key_switches_wifi_keeps_identity(tmp_path):
    """STA_NO を空にしたり hostnamectl したりしない。"""
    repo = tmp_path / "new"
    repo.mkdir()
    (repo / ".kit").mkdir()
    (repo / ".kit" / "ap-join.env").write_text(
        "AP_SSID=sibling-hub\nWIFI_AP_PSK=pskpskpsk\n", encoding="utf-8"
    )
    inv = repo / "fleet" / "children.conf"
    inv.parent.mkdir()
    inv.write_text("# empty\n", encoding="utf-8")
    old_inv = ["# old\n", "zero2\n", "other.local\n"]
    old_inv_text = {"body": "".join(old_inv)}

    def script(joined, remote):
        if joined.startswith("ssh") and "cat " in remote and "children.conf" in remote:
            return old_inv_text["body"] + f"{CAT_OK}\n"
        if "wlan0/address" in remote or "wlan0/address" in joined:
            return "aa:bb:cc:dd:ee:ff\n"
        if "authorized_keys" in remote:
            return f"{KEY_OK}\n"
        if "nmcli" in remote:
            return ""
        if "printf" in remote and "children.conf" in remote:
            # write-back of stripped inventory
            return ""
        if "ssh-keyscan" in joined:
            return "hostkey-line\n"
        return ""

    runner = _recorder(script)
    waited = []

    def wait_fn(mac, **kwargs):
        waited.append(mac)
        from fleet_ui.provision import StepResult
        return StepResult(ok=True, message="復帰", output="10.42.0.9")

    known = tmp_path / "known_hosts"
    known.write_text("", encoding="utf-8")

    res = take_child(
        old_host="172.22.13.17",
        entry="zero2",
        repo=repo,
        pubkey="ssh-ed25519 AAAA newhub",
        runner=runner,
        wait_fn=wait_fn,
        known_hosts=known,
        inventory_path=inv,
        remote_inventory="~/projects/presence-logger/fleet/children.conf",
    )
    assert res.ok, res.message
    joined = "\n".join(" ".join(c) for c in runner.calls)
    assert "id_names_config" not in joined
    assert "hostnamectl" not in joined
    assert "blank" not in joined
    assert "authorized_keys" in joined
    assert KEY_OK in joined
    assert "nmcli" in joined
    assert "autoconnect-priority" in joined
    assert "sibling-hub" in joined
    assert "ssh-ed25519 AAAA newhub" in joined
    assert waited == ["aa:bb:cc:dd:ee:ff"]
    body = inv.read_text(encoding="utf-8")
    assert "zero2.local" in body


def test_take_child_rejects_unsafe_old_host():
    from fleet_ui.provision import StepResult
    res = take_child(
        old_host="host; rm",
        entry="zero2",
        repo=Path("."),
        pubkey="ssh-ed25519 AAAA x",
        runner=lambda cmd: "",
        wait_fn=lambda mac, **k: StepResult(ok=True, message=""),
    )
    assert not res.ok
    assert "旧親" in res.message or "ホスト" in res.message


def test_list_children_payload_returns_entries_without_psk():
    def run(cmd):
        return f"zero2\nother.local\n{CAT_OK}\n"

    payload = list_children_payload("172.22.13.17", runner=run)
    assert payload["ok"] is True
    assert [c["entry"] for c in payload["children"]] == ["zero2", "other.local"]
    assert payload["children"][0]["name"] == "zero2.local"
    assert "psk" not in payload


def test_list_remote_children_comments_only_is_ok():
    def run(cmd):
        return f"# header\n\n{CAT_OK}\n"

    res = list_remote_children("172.22.13.17", runner=run)
    assert res.ok
    assert res.message == "0 台"


def test_list_remote_children_without_sentinel_is_ssh_failure():
    res = list_remote_children("172.22.13.17", runner=lambda cmd: "")
    assert not res.ok
    assert "子一覧" in res.message


def test_take_child_rejects_name_already_on_this_hub(tmp_path):
    repo = tmp_path / "new"
    repo.mkdir()
    (repo / ".kit").mkdir()
    (repo / ".kit" / "ap-join.env").write_text(
        "AP_SSID=sibling-hub\nWIFI_AP_PSK=pskpskpsk\n", encoding="utf-8"
    )
    inv = repo / "fleet" / "children.conf"
    inv.parent.mkdir()
    inv.write_text("zero2.local\n", encoding="utf-8")
    from fleet_ui.provision import StepResult
    res = take_child(
        old_host="172.22.13.17",
        entry="zero2",
        repo=repo,
        pubkey="ssh-ed25519 AAAA newhub",
        runner=lambda cmd: "",
        wait_fn=lambda mac, **k: StepResult(ok=True, message=""),
        inventory_path=inv,
    )
    assert not res.ok
    assert "既に使われています" in res.message


def _take_repo(tmp_path):
    repo = tmp_path / "new"
    repo.mkdir()
    (repo / ".kit").mkdir()
    (repo / ".kit" / "ap-join.env").write_text(
        "AP_SSID=sibling-hub\nWIFI_AP_PSK=pskpskpsk\n", encoding="utf-8"
    )
    inv = repo / "fleet" / "children.conf"
    inv.parent.mkdir()
    inv.write_text("# empty\n", encoding="utf-8")
    return repo, inv


def test_take_child_does_not_switch_wifi_without_key_ok(tmp_path):
    repo, inv = _take_repo(tmp_path)

    def script(joined, remote):
        if "wlan0/address" in remote or "wlan0/address" in joined:
            return "aa:bb:cc:dd:ee:ff\n"
        if "authorized_keys" in remote:
            return "permission denied\n"
        return ""

    runner = _recorder(script)
    from fleet_ui.provision import StepResult
    res = take_child(
        old_host="172.22.13.17",
        entry="zero2",
        repo=repo,
        pubkey="ssh-ed25519 AAAA newhub",
        runner=runner,
        wait_fn=lambda mac, **k: StepResult(ok=True, message=""),
        inventory_path=inv,
    )
    assert not res.ok
    assert "公開鍵" in res.message
    joined = "\n".join(" ".join(c) for c in runner.calls)
    assert "nmcli" not in joined
    assert "zero2.local" not in inv.read_text(encoding="utf-8")


def test_take_child_does_not_wipe_old_inventory_when_cat_fails(tmp_path):
    repo, inv = _take_repo(tmp_path)
    writes = []

    def script(joined, remote):
        if "wlan0/address" in remote or "wlan0/address" in joined:
            return "aa:bb:cc:dd:ee:ff\n"
        if "authorized_keys" in remote:
            return f"{KEY_OK}\n"
        if "nmcli" in remote:
            return ""
        if " > " in remote and "children.conf" in remote:
            writes.append(remote)
            return ""
        if "children.conf" in remote:
            return ""
        if "ssh-keyscan" in joined:
            return "hostkey-line\n"
        return ""

    runner = _recorder(script)
    known = tmp_path / "known_hosts"
    known.write_text("", encoding="utf-8")
    from fleet_ui.provision import StepResult
    res = take_child(
        old_host="172.22.13.17",
        entry="zero2",
        repo=repo,
        pubkey="ssh-ed25519 AAAA newhub",
        runner=runner,
        wait_fn=lambda mac, **k: StepResult(ok=True, message="復帰", output="10.42.0.9"),
        known_hosts=known,
        inventory_path=inv,
        remote_inventory="~/projects/presence-logger/fleet/children.conf",
    )
    assert res.ok, res.message
    assert writes == []
    assert "手動削除" in res.message
    assert "zero2.local" in inv.read_text(encoding="utf-8")


def test_take_child_keyscans_ip_when_hostname_mdns_is_empty(tmp_path):
    repo, inv = _take_repo(tmp_path)
    scanned = []

    def script(joined, remote):
        if "wlan0/address" in remote or "wlan0/address" in joined:
            return "aa:bb:cc:dd:ee:ff\n"
        if "authorized_keys" in remote:
            return f"{KEY_OK}\n"
        if "nmcli" in remote:
            return ""
        if "cat " in remote and "children.conf" in remote:
            return f"zero2\n{CAT_OK}\n"
        if "ssh-keyscan" in joined:
            scanned.append(joined)
            if "10.42.0.9" in joined:
                return "ip-hostkey\n"
            return ""
        return ""

    runner = _recorder(script)
    known = tmp_path / "known_hosts"
    known.write_text("", encoding="utf-8")
    from fleet_ui.provision import StepResult
    res = take_child(
        old_host="172.22.13.17",
        entry="zero2",
        repo=repo,
        pubkey="ssh-ed25519 AAAA newhub",
        runner=runner,
        wait_fn=lambda mac, **k: StepResult(ok=True, message="復帰", output="10.42.0.9"),
        known_hosts=known,
        inventory_path=inv,
        remote_inventory="~/projects/presence-logger/fleet/children.conf",
    )
    assert res.ok, res.message
    assert any("10.42.0.9" in s for s in scanned)
    assert "ip-hostkey" in known.read_text(encoding="utf-8")
