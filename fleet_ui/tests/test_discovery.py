"""AP配下の端末とインベントリの突き合わせの検証。

個体の主キーは MAC。IPは親APのDHCPで変わり、ホスト名はSDカードのコピーで衝突する
(実際に2台目投入時、両機とも pizero2w になり MQTT の client_id が衝突した)。
"""
from fleet_ui.discovery import classify, parse_neigh, resolve_inventory_ips

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
