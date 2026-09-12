"""AP配下の端末とインベントリの突き合わせの検証。

個体の主キーは MAC。IPは親APのDHCPで変わり、ホスト名はSDカードのコピーで衝突する
(実際に2台目投入時、両機とも pizero2w になり MQTT の client_id が衝突した)。
"""
from fleet_ui.discovery import (
    classify,
    current_ap_dev,
    learned_macs,
    load_known_macs,
    parse_neigh,
    resolve_inventory_ips,
    save_known_macs,
)

SAMPLE = """10.42.0.52 lladdr 2c:cf:67:c1:1d:7b STALE
10.42.0.194 lladdr 88:a2:9e:30:5e:46 REACHABLE
10.42.0.99 lladdr aa:bb:cc:dd:ee:ff FAILED
"""


def test_parse_neigh_extracts_ip_and_mac():
    ns = parse_neigh(SAMPLE)
    assert (ns[0].ip, ns[0].mac) == ("10.42.0.52", "2c:cf:67:c1:1d:7b")


def test_parse_neigh_skips_failed_entries():
    """FAILED は「居ない」。登録候補として出すと存在しない端末を操作しかける。"""
    assert all(n.ip != "10.42.0.99" for n in parse_neigh(SAMPLE))


def test_parse_neigh_ignores_garbage_lines():
    assert parse_neigh("これは出力ではない\n\n") == []


def test_known_child_is_classified_known():
    ns = parse_neigh(SAMPLE)
    rows = classify(ns, {"zero2": "10.42.0.52"})
    known = [r for r in rows if r.kind == "known"]
    assert len(known) == 1
    assert (known[0].entry, known[0].mac) == ("zero2", "2c:cf:67:c1:1d:7b")


def test_unlisted_device_is_classified_new():
    ns = parse_neigh(SAMPLE)
    rows = classify(ns, {"zero2": "10.42.0.52"})
    new = [r for r in rows if r.kind == "new"]
    assert [r.ip for r in new] == ["10.42.0.194"]


def test_unresolvable_entry_is_not_treated_as_new():
    """IPを解決できない既存の子を新機と誤認してはいけない。

    誤認すると登録ウィザードで稼働中の機体を再プロビジョニングしかねない。
    """
    rows = classify(parse_neigh(SAMPLE), {"zero2": "10.42.0.52", "kodomo3": None})
    unresolved = [r for r in rows if r.kind == "unresolved"]
    assert [r.entry for r in unresolved] == ["kodomo3"]


def test_live_child_whose_ip_drifted_is_not_offered_for_registration():
    """稼働中の子が登録候補に出てはいけない。

    ssh_config は `HostName 10.42.0.52` のようにIPをハードコードする。実IPが
    DHCPでドリフトすると、その項目は unresolved になる一方、同じ個体のARP行は
    誰にも claim されず new に落ちる。そのまま登録すると本番機の送信停止・
    STA_NO消去・改名・再起動が走る。
    """
    arp = parse_neigh("10.42.0.77 lladdr 2c:cf:67:c1:1d:7b REACHABLE\n")
    rows = classify(arp, {"zero2": "10.42.0.52"})
    assert [r.kind for r in rows if r.mac == "2c:cf:67:c1:1d:7b"] == ["unverified"]
    assert not [r for r in rows if r.kind == "new"]


def test_no_registration_candidates_while_any_entry_is_unresolved():
    """絵が不完全な間は登録候補を出さない。

    unresolved が1つでもある限り、ARP上のどの端末が既知の子なのか断定できない。
    """
    arp = parse_neigh(SAMPLE)
    rows = classify(arp, {"zero2": "10.42.0.52", "kodomo3": None})
    assert not [r for r in rows if r.kind == "new"]
    assert [r.ip for r in rows if r.kind == "unverified"] == ["10.42.0.194"]


def test_new_devices_are_offered_once_every_entry_resolves():
    """全項目が解決してARPと一致していれば、残りは本当に未知の端末。"""
    arp = parse_neigh(SAMPLE)
    rows = classify(arp, {"zero2": "10.42.0.52"})
    assert [r.ip for r in rows if r.kind == "new"] == ["10.42.0.194"]
    assert not [r for r in rows if r.kind == "unverified"]


def test_entry_resolving_to_absent_ip_is_unresolved():
    """インベントリにあるがARPに居ない子も unresolved(到達不能)として出す。"""
    rows = classify(parse_neigh(SAMPLE), {"zero2": "10.42.0.52", "kodomo3": "10.42.0.77"})
    assert [r.entry for r in rows if r.kind == "unresolved"] == ["kodomo3"]


def test_cloned_hosts_are_distinguished_by_mac():
    """同名クローンでもMACで別個体として扱えること。"""
    text = ("10.42.0.52 lladdr 2c:cf:67:c1:1d:7b REACHABLE\n"
            "10.42.0.194 lladdr 88:a2:9e:30:5e:46 REACHABLE\n")
    rows = classify(parse_neigh(text), {"zero2": "10.42.0.52"})
    macs = {r.mac for r in rows if r.mac}
    assert macs == {"2c:cf:67:c1:1d:7b", "88:a2:9e:30:5e:46"}


def test_resolve_uses_getent_then_ssh_config():
    """mDNS名は getent、ssh_config の別名は `ssh -G` で解決する。

    実機では pizero2w-2.local は getent で引けるが、zero2 は ssh_config の
    別名なので引けず、`ssh -G` の hostname 行から取る必要がある。
    """
    calls = []

    def fake_runner(cmd):
        calls.append(cmd)
        if cmd[0] == "getent":
            return "10.42.0.194 pizero2w-2.local\n" if "pizero2w-2.local" in cmd else ""
        if cmd[0] == "ssh":
            return "user pi\nhostname 10.42.0.52\nport 22\n"
        return ""

    got = resolve_inventory_ips(["pizero2w-2.local", "zero2"], runner=fake_runner)
    assert got == {"pizero2w-2.local": "10.42.0.194", "zero2": "10.42.0.52"}
    assert any(c[0] == "ssh" for c in calls)


def test_resolve_returns_none_when_unresolvable():
    got = resolve_inventory_ips(["nowhere"], runner=lambda cmd: "")
    assert got == {"nowhere": None}


# --- TOFU(Trust On First Use)によるMAC照合 -----------------------------------
# IPのみでの突き合わせは、正規のIPを別デバイスが奪った場合(Variant A)や
# 2エントリが同じIPを指す設定ミス(Variant B)で、稼働中の子を new(登録候補)に
# 落としてしまう(最終レビューで指摘・再現済み)。known_macs は entry->mac の
# 過去の確認結果で、SSHのknown_hostsと同じ信頼モデル(初回は信頼し、以後は照合)。

def test_first_contact_trusts_and_can_be_learned():
    """known_macs が空のとき(初回)は従来どおりIPで信頼し、学習対象として返す。"""
    rows = classify(parse_neigh(SAMPLE), {"zero2": "10.42.0.52"}, known_macs={})
    known = [r for r in rows if r.kind == "known"]
    assert [(r.entry, r.mac) for r in known] == [("zero2", "2c:cf:67:c1:1d:7b")]
    assert learned_macs(rows, {}) == {"zero2": "2c:cf:67:c1:1d:7b"}


def test_already_trusted_mac_is_not_relearned():
    known_macs = {"zero2": "2c:cf:67:c1:1d:7b"}
    rows = classify(parse_neigh(SAMPLE), {"zero2": "10.42.0.52"}, known_macs=known_macs)
    assert learned_macs(rows, known_macs) == {}


def test_ip_stolen_by_another_device_does_not_expose_the_real_child_as_new():
    """Variant A: 正規のIPを別デバイスが占有し、本物は別IPに居る。

    最終レビューで実際に再現された形。稼働中の子(本物のMAC)は known のまま保たれ、
    IPを奪った側は正体不明の new として正直に表示される(=誤って安全と偽らない)。
    """
    arp = parse_neigh(
        "10.42.0.52 lladdr aa:aa:aa:aa:aa:aa REACHABLE\n"
        "10.42.0.194 lladdr b8:27:eb:11:11:11 REACHABLE\n"
    )
    known_macs = {"zero2": "b8:27:eb:11:11:11"}   # 過去に確認済みの本物のMAC
    rows = classify(arp, {"zero2": "10.42.0.52"}, known_macs=known_macs)
    known = {(r.entry, r.mac, r.ip) for r in rows if r.kind == "known"}
    assert known == {("zero2", "b8:27:eb:11:11:11", "10.42.0.194")}
    # IPを奪った側は new として出る(zero2 として誤登録されることはない)。
    new_macs = {r.mac for r in rows if r.kind == "new"}
    assert new_macs == {"aa:aa:aa:aa:aa:aa"}
    assert "b8:27:eb:11:11:11" not in new_macs


def test_swapped_leases_between_two_known_children_resolve_correctly():
    """Variant B: 2子のインベントリが同じIPを指す(DHCPリース入れ替わり等)。

    IPだけでは区別できないが、記録済みMACで両方とも正しい個体へ解決できる。
    """
    arp = parse_neigh(
        "10.42.0.52 lladdr bbbbbbbbbbbb REACHABLE\n"
        "10.42.0.194 lladdr aaaaaaaaaaaa REACHABLE\n".replace("bbbbbbbbbbbb", "b8:27:eb:22:22:22")
        .replace("aaaaaaaaaaaa", "b8:27:eb:11:11:11")
    )
    known_macs = {"zero2": "b8:27:eb:11:11:11", "pizero2w-2.local": "b8:27:eb:22:22:22"}
    rows = classify(
        arp, {"zero2": "10.42.0.52", "pizero2w-2.local": "10.42.0.52"}, known_macs=known_macs
    )
    known = {(r.entry, r.ip) for r in rows if r.kind == "known"}
    assert known == {("zero2", "10.42.0.194"), ("pizero2w-2.local", "10.42.0.52")}
    assert not [r for r in rows if r.kind in ("new", "unverified")]


def test_moved_child_is_not_flagged_unresolved():
    """記録済みMACの子がIPを変えても(ドリフト)、到達不能扱いにしない。"""
    arp = parse_neigh("10.42.0.77 lladdr 2c:cf:67:c1:1d:7b REACHABLE\n")
    known_macs = {"zero2": "2c:cf:67:c1:1d:7b"}
    rows = classify(arp, {"zero2": "10.42.0.52"}, known_macs=known_macs)
    assert [(r.kind, r.ip) for r in rows] == [("known", "10.42.0.77")]


def test_missing_known_child_with_no_replacement_is_unresolved():
    """記録済みの子がARPのどこにも見当たらなければ、従来どおり unresolved。"""
    known_macs = {"kodomo3": "aa:bb:cc:dd:ee:ff"}
    rows = classify(parse_neigh(SAMPLE), {"kodomo3": None}, known_macs=known_macs)
    assert [r.kind for r in rows if r.entry == "kodomo3"] == ["unresolved"]


def test_classify_without_known_macs_keeps_prior_behaviour():
    """known_macs 省略時は従来どおり(後方互換)。"""
    rows_a = classify(parse_neigh(SAMPLE), {"zero2": "10.42.0.52"})
    rows_b = classify(parse_neigh(SAMPLE), {"zero2": "10.42.0.52"}, known_macs=None)
    assert rows_a == rows_b


def test_save_and_load_known_macs_round_trip(tmp_path):
    p = tmp_path / "known_macs.json"
    save_known_macs({"zero2": "2c:cf:67:c1:1d:7b"}, path=p)
    assert load_known_macs(path=p) == {"zero2": "2c:cf:67:c1:1d:7b"}


def test_load_known_macs_missing_file_is_empty(tmp_path):
    assert load_known_macs(path=tmp_path / "nope.json") == {}


def test_load_known_macs_ignores_corrupt_file(tmp_path):
    p = tmp_path / "known_macs.json"
    p.write_text("not json", encoding="utf-8")
    assert load_known_macs(path=p) == {}


def test_current_ap_dev_reads_env(monkeypatch):
    monkeypatch.setenv("AP_DEV", "wlan0")
    assert current_ap_dev() == "wlan0"


def test_current_ap_dev_rejects_injection(monkeypatch):
    monkeypatch.setenv("AP_DEV", "wlan0; rm")
    assert current_ap_dev() == "wlan1"
