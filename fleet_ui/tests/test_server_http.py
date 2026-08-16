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


def _post(port, headers, body):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST", "/api/step", body=json.dumps(body), headers=headers)
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
