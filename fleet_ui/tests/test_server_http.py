"""HTTPレイヤー(CSRFヘッダ・Hostヘッダ)の検証。

最終レビューで、CSRF対策(X-Fleet-UIヘッダ)にHTTP層のテストが一切無いことが
指摘された。「セキュリティ機構はテストが無ければ、次のリファクタで
静かに消える」ため、ここではソケット越しに本物のリクエストを送り、実際に
ガードが効くことを確認する。run_step は差し替えて、実ネットワーク/SSHには
一切触れない(稼働中の実機・fleet-ui.serviceには触れない)。
"""
import http.client
import json
import threading

import fleet_ui.server as server_mod


def _start_server(monkeypatch, run_step_fn):
    monkeypatch.setattr(server_mod, "run_step", run_step_fn)
    srv = server_mod.make_server(host="127.0.0.1", port=0)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, port


def _post(port, headers, body, path="/api/step"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST", path, body=json.dumps(body), headers=headers)
        r = conn.getresponse()
        return r.status, json.loads(r.read())
    finally:
        conn.close()


def test_post_without_csrf_header_is_rejected(monkeypatch):
    calls = []
    srv, port = _start_server(
        monkeypatch, lambda req: (calls.append(req), {"ok": True, "message": ""})[1]
    )
    try:
        status, body = _post(port, {"Content-Type": "application/json"}, {"step": "stop"})
        assert status == 403
        assert body["ok"] is False
        assert calls == []  # run_step は一切呼ばれない
    finally:
        srv.shutdown()


def test_post_with_csrf_header_is_dispatched(monkeypatch):
    calls = []
    srv, port = _start_server(
        monkeypatch,
        lambda req: (calls.append(req), {"ok": True, "message": "done"})[1],
    )
    try:
        status, body = _post(
            port,
            {"Content-Type": "application/json", "X-Fleet-UI": "1"},
            {"step": "stop", "ip": "10.42.0.9"},
        )
        assert status == 200
        assert body == {"ok": True, "message": "done"}
        assert calls == [{"step": "stop", "ip": "10.42.0.9"}]
    finally:
        srv.shutdown()


def test_post_with_wrong_host_header_is_rejected_even_with_csrf(monkeypatch):
    """DNS rebinding対策。Host が期待値でなければCSRFヘッダがあっても拒否する。"""
    calls = []
    srv, port = _start_server(monkeypatch, lambda req: (calls.append(req), {"ok": True})[1])
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.putrequest("POST", "/api/step", skip_host=True)
        conn.putheader("Host", "evil.com")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("X-Fleet-UI", "1")
        body = json.dumps({"step": "stop"}).encode()
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        conn.send(body)
        r = conn.getresponse()
        assert r.status == 403
        r.read()
        conn.close()
        assert calls == []
    finally:
        srv.shutdown()


def test_get_with_wrong_host_header_is_also_rejected(monkeypatch):
    """GET(index.html / /api/fleet)もHostヘッダのガード対象。"""
    srv, port = _start_server(monkeypatch, lambda req: {"ok": True})
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.putrequest("GET", "/", skip_host=True)
        conn.putheader("Host", "evil.com")
        conn.endheaders()
        r = conn.getresponse()
        assert r.status == 403
        r.read()
        conn.close()
    finally:
        srv.shutdown()


def test_migrate_list_without_csrf_header_is_rejected(monkeypatch):
    calls = []
    monkeypatch.setattr(
        server_mod.migrate,
        "list_children_payload",
        lambda *a, **k: calls.append((a, k)) or {"ok": True},
    )
    srv, port = _start_server(monkeypatch, lambda req: {"ok": True})
    try:
        status, body = _post(
            port,
            {"Content-Type": "application/json"},
            {"old_host": "172.22.13.17"},
            path="/api/migrate/list",
        )
        assert status == 403
        assert body["ok"] is False
        assert calls == []
    finally:
        srv.shutdown()


def test_migrate_list_with_csrf_header_is_dispatched(monkeypatch):
    calls = []
    monkeypatch.setattr(
        server_mod.migrate,
        "list_children_payload",
        lambda old_host, **k: (
            calls.append(old_host),
            {
                "ok": True,
                "message": "1 台",
                "children": [{"entry": "zero2", "name": "zero2.local"}],
                "pubkey": "ssh-ed25519 AAAA x",
            },
        )[1],
    )
    srv, port = _start_server(monkeypatch, lambda req: {"ok": True})
    try:
        status, body = _post(
            port,
            {"Content-Type": "application/json", "X-Fleet-UI": "1"},
            {"old_host": "172.22.13.17"},
            path="/api/migrate/list",
        )
        assert status == 200
        assert body["ok"] is True
        assert body["children"][0]["entry"] == "zero2"
        assert calls == ["172.22.13.17"]
        dumped = json.dumps(body)
        assert "WIFI_AP_PSK" not in dumped
        assert "password" not in dumped.lower()
    finally:
        srv.shutdown()


def test_migrate_take_without_csrf_header_is_rejected(monkeypatch):
    calls = []
    monkeypatch.setattr(
        server_mod.migrate,
        "take_payload",
        lambda *a, **k: calls.append((a, k)) or {"ok": True},
    )
    srv, port = _start_server(monkeypatch, lambda req: {"ok": True})
    try:
        status, body = _post(
            port,
            {"Content-Type": "application/json"},
            {"old_host": "172.22.13.17", "entry": "zero2"},
            path="/api/migrate/take",
        )
        assert status == 403
        assert calls == []
        assert body["ok"] is False
    finally:
        srv.shutdown()


def test_migrate_take_with_csrf_header_is_dispatched(monkeypatch):
    calls = []
    monkeypatch.setattr(
        server_mod.migrate,
        "take_payload",
        lambda old_host, entry, **k: (
            calls.append((old_host, entry)),
            {"ok": True, "message": "zero2 をこのハブへ移しました", "output": "10.42.0.9"},
        )[1],
    )
    srv, port = _start_server(monkeypatch, lambda req: {"ok": True})
    try:
        status, body = _post(
            port,
            {"Content-Type": "application/json", "X-Fleet-UI": "1"},
            {"old_host": "172.22.13.17", "entry": "zero2"},
            path="/api/migrate/take",
        )
        assert status == 200
        assert body["ok"] is True
        assert calls == [("172.22.13.17", "zero2")]
        dumped = json.dumps(body)
        assert "WIFI_AP_PSK" not in dumped
        assert "secret" not in dumped.lower()
    finally:
        srv.shutdown()


def test_get_fleet_includes_migrate_without_psk(monkeypatch):
    monkeypatch.setattr(server_mod, "_read_inventory", lambda: [])
    monkeypatch.setattr(server_mod, "resolve_inventory_ips", lambda entries: {})
    monkeypatch.setattr(server_mod, "read_neighbors", lambda: [])
    monkeypatch.setattr(server_mod, "load_known_macs", lambda: {})
    monkeypatch.setattr(
        server_mod.migrate,
        "migrate_status",
        lambda **k: {
            "pubkey": "ssh-ed25519 AAAA x",
            "ap_ssid": "sibling-hub",
            "has_psk": True,
        },
    )
    srv, port = _start_server(monkeypatch, lambda req: {"ok": True})
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/fleet")
        r = conn.getresponse()
        assert r.status == 200
        body = json.loads(r.read())
        conn.close()
        assert body["migrate"]["ap_ssid"] == "sibling-hub"
        assert body["migrate"]["has_psk"] is True
        dumped = json.dumps(body)
        assert "WIFI_AP_PSK" not in dumped
        assert set(body["migrate"]) == {"pubkey", "ap_ssid", "has_psk"}
    finally:
        srv.shutdown()
