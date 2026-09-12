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
