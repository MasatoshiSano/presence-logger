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
    assert body["hostname"] == "tpc12345-002"


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


# --- B2-T16: align(子の送り先を揃え、引用符つきプロファイルは報告だけ) -----------------


def _align_output(gw, *, join="ok", active="active", host_now=None):
    env = "" if gw == "10.42.0.1" else f"MQTT_HOST={gw}"
    return (
        f"HOST_NOW={host_now or gw}\nENV_NOW={env}\nACTIVE_NOW={active}\nJOIN_PROFILE={join}\n"
    )


def _patch_align(monkeypatch, per_host, gw="10.42.1.1"):
    from fleet_ui.provision import StepResult

    calls = []

    def fake_align(host, want, *, runner):
        calls.append((host, want))
        out = per_host[host]
        ok = f"HOST_NOW={want}" in out
        return StepResult(ok=ok, message="" if ok else "host が違います", output=out)

    monkeypatch.setattr(child_cli.send_target, "align_send_target", fake_align)
    monkeypatch.setattr(child_cli.send_target, "load_ap_gw_ip", lambda repo=None: gw)
    return calls


def test_align_without_args_covers_whole_inventory_and_reports(monkeypatch, capsys):
    monkeypatch.setattr(child_cli, "_inventory_entries", lambda: ["zero2.local", "zero3.local"])
    calls = _patch_align(monkeypatch, {
        "zero2.local": _align_output("10.42.1.1", join="quoted"),
        "zero3.local": _align_output("10.42.1.1", join="ok"),
    })
    assert child_cli.main(["align"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert calls == [("zero2.local", "10.42.1.1"), ("zero3.local", "10.42.1.1")]
    assert body["ok"] is True
    assert body["gw"] == "10.42.1.1"
    rows = {r["entry"]: r for r in body["children"]}
    assert rows["zero2.local"]["host_now"] == "10.42.1.1"
    assert rows["zero2.local"]["join_profile"] == "quoted"
    assert rows["zero3.local"]["join_profile"] == "ok"
    assert "quoted" in body["message"] or "引用符" in body["message"]


def test_align_does_not_repair_quoted_profile(monkeypatch, capsys):
    """§1.5: PSK をフリートへ流す操作を増やさない。報告だけで、直す呼び出しをしない。"""
    monkeypatch.setattr(child_cli, "_inventory_entries", lambda: ["zero2.local"])
    calls = _patch_align(monkeypatch, {"zero2.local": _align_output("10.42.1.1", join="quoted")})
    called = []
    monkeypatch.setattr(child_cli.migrate, "take_child", lambda *a, **k: called.append("take"))
    monkeypatch.setattr(child_cli.migrate, "nm_join_keyfile", lambda *a, **k: called.append("kf"))
    assert child_cli.main(["align"]) == 0
    assert called == []
    assert calls == [("zero2.local", "10.42.1.1")]  # SSH は align の1回だけ
    out = capsys.readouterr()
    assert "psk" not in (out.out + out.err).lower()


def test_align_with_entries_normalizes_names(monkeypatch, capsys):
    monkeypatch.setattr(child_cli, "_inventory_entries", lambda: ["zero2.local", "zero3.local"])
    calls = _patch_align(monkeypatch, {"zero3.local": _align_output("10.42.1.1")})
    assert child_cli.main(["align", "zero3"]) == 0
    assert calls == [("zero3.local", "10.42.1.1")]
    body = json.loads(capsys.readouterr().out)
    assert [r["entry"] for r in body["children"]] == ["zero3.local"]


def test_align_failure_of_one_child_is_not_ok_but_continues(monkeypatch, capsys):
    monkeypatch.setattr(child_cli, "_inventory_entries", lambda: ["zero2.local", "zero3.local"])
    calls = _patch_align(monkeypatch, {
        "zero2.local": _align_output("10.42.1.1", host_now="10.42.0.1"),
        "zero3.local": _align_output("10.42.1.1"),
    })
    assert child_cli.main(["align"]) == 1
    body = json.loads(capsys.readouterr().out)
    assert len(calls) == 2  # 1台の失敗で残りを止めない
    assert body["ok"] is False
    rows = {r["entry"]: r for r in body["children"]}
    assert rows["zero2.local"]["ok"] is False
    assert rows["zero3.local"]["ok"] is True


def test_align_with_empty_inventory_is_not_ok(monkeypatch, capsys):
    monkeypatch.setattr(child_cli, "_inventory_entries", lambda: [])
    _patch_align(monkeypatch, {})
    assert child_cli.main(["align"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_align_bad_site_env_gw_fails_before_ssh(monkeypatch, capsys):
    monkeypatch.setattr(child_cli, "_inventory_entries", lambda: ["zero2.local"])
    calls = _patch_align(monkeypatch, {"zero2.local": _align_output("10.42.1.1")})

    def bad(repo=None):
        raise ValueError("site.env の AP_GW_IP が IPv4 ではありません: 'x'")

    monkeypatch.setattr(child_cli.send_target, "load_ap_gw_ip", bad)
    assert child_cli.main(["align"]) == 1
    assert calls == []
    assert "AP_GW_IP" in json.loads(capsys.readouterr().out)["message"]


def test_align_is_listed_in_usage(capsys):
    assert child_cli.main([]) == 2
    assert "align" in capsys.readouterr().err
