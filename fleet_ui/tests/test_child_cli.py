"""ターミナル用 child_cli の検証。PSK を標準出力へ出さない。"""
import json

from fleet_ui import child_cli


def test_status_never_includes_psk(monkeypatch, capsys):
    monkeypatch.setattr(
        child_cli.migrate,
        "migrate_status",
        lambda **k: {"pubkey": "ssh-ed25519 AAAA x", "ap_ssid": "sibling-hub", "has_psk": True},
    )
    assert child_cli.main(["status"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True
    assert body["ap_ssid"] == "sibling-hub"
    assert "WIFI_AP_PSK" not in json.dumps(body)
    assert "secret" not in json.dumps(body).lower()


def test_list_returns_children_json(monkeypatch, capsys):
    monkeypatch.setattr(
        child_cli.migrate,
        "list_children_payload",
        lambda host, **k: {
            "ok": True,
            "message": "1 台",
            "children": [{"entry": "zero2", "name": "zero2.local"}],
            "pubkey": "ssh-ed25519 AAAA x",
        },
    )
    assert child_cli.main(["list", "172.22.13.17"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["children"][0]["entry"] == "zero2"


def test_list_without_host_is_not_ok(capsys):
    assert child_cli.main(["list"]) == 1
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is False


def test_take_dispatches(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        child_cli.migrate,
        "take_payload",
        lambda old, entry, **k: (
            calls.append((old, entry)),
            {"ok": True, "message": "移しました", "output": ""},
        )[1],
    )
    assert child_cli.main(["take", "172.22.13.17", "zero2"]) == 0
    assert calls == [("172.22.13.17", "zero2")]
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True


def test_unknown_command_is_not_ok(capsys):
    assert child_cli.main(["nope"]) == 1
    body = json.loads(capsys.readouterr().out)
    assert "不明なコマンド" in body["message"]


def test_candidates_skips_unverified_and_known(monkeypatch, capsys):
    from types import SimpleNamespace

    monkeypatch.setattr(child_cli, "_inventory_entries", lambda: ["zero2.local"])
    monkeypatch.setattr(
        child_cli, "resolve_inventory_ips", lambda entries: {"zero2.local": "10.42.0.10"}
    )
    monkeypatch.setattr(child_cli, "load_known_macs", lambda: {"zero2.local": "aa:bb:cc:dd:ee:ff"})
    monkeypatch.setattr(child_cli, "read_neighbors", lambda: [])
    monkeypatch.setattr(
        child_cli,
        "classify",
        lambda *a, **k: [
            SimpleNamespace(kind="known", ip="10.42.0.10", mac="aa:bb:cc:dd:ee:ff", entry="zero2.local"),
            SimpleNamespace(kind="unverified", ip="10.42.0.20", mac="11:22:33:44:55:66", entry=None),
        ],
    )
    monkeypatch.setattr(child_cli.provision, "probe_hostname", lambda ip, **k: "drifted")
    assert child_cli.main(["candidates"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True
    assert body["candidates"] == []


def test_candidates_includes_kind_new(monkeypatch, capsys):
    from types import SimpleNamespace

    monkeypatch.setattr(child_cli, "_inventory_entries", lambda: [])
    monkeypatch.setattr(child_cli, "resolve_inventory_ips", lambda entries: {})
    monkeypatch.setattr(child_cli, "load_known_macs", lambda: {})
    monkeypatch.setattr(child_cli, "read_neighbors", lambda: [])
    monkeypatch.setattr(
        child_cli,
        "classify",
        lambda *a, **k: [
            SimpleNamespace(kind="new", ip="10.42.0.9", mac="aa:bb:cc:dd:ee:01", entry=None),
        ],
    )
    monkeypatch.setattr(child_cli.provision, "probe_hostname", lambda ip, **k: "zero2")
    assert child_cli.main(["candidates"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["candidates"][0]["hostname"] == "zero2"
    assert body["candidates"][0]["ssh_ok"] is True
    assert body["candidates"][0]["kind"] == "new"


def test_suggest_returns_hostname(monkeypatch, capsys):
    monkeypatch.setattr(child_cli, "_inventory_entries", lambda: ["zero2.local"])
    monkeypatch.setattr(child_cli, "_hub_hostname", lambda: "tpc12345")
    assert child_cli.main(["suggest"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True
    assert body["hostname"] == "tpc12345-2"


def test_register_dispatches(monkeypatch, capsys):
    from fleet_ui.provision import StepResult
    calls = []
    monkeypatch.setattr(child_cli, "_inventory_entries", lambda: [])
    monkeypatch.setattr(
        child_cli.provision,
        "register_new_child",
        lambda ip, mac, name, **k: (
            calls.append((ip, mac, name)),
            StepResult(ok=True, message="登録しました", output="10.42.0.80"),
        )[1],
    )
    assert child_cli.main(["register", "10.42.0.9", "aa:bb:cc:dd:ee:01", "pizero2w-3"]) == 0
    assert calls == [("10.42.0.9", "aa:bb:cc:dd:ee:01", "pizero2w-3")]
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True
