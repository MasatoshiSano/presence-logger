# ruff: noqa: T201  これは操作用のサーバなので print 出力は意図的
"""フリート管理UIのHTTP配線。

ロジックは discovery / collect / hostname / provision に置き、ここは配線と
JSONの組み立てだけにする。

127.0.0.1 にのみ bind する。この画面は子へ SSH+sudo できるため、ネットワークへ
露出させない。操作者は `ssh -L 8090:localhost:8090 pi@親` で見る。
"""
from __future__ import annotations

import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fleet_ui import migrate, provision
from fleet_ui.collect import ChildStatus, collect_status
from fleet_ui.discovery import (
    classify,
    learned_macs,
    load_known_macs,
    read_neighbors,
    resolve_inventory_ips,
    save_known_macs,
)
from fleet_ui.hostname import read_hub_hostname, suggest_hostname, validate_hostname


def _load_sta_no_report():
    """scripts/lib/sta_no_report.py を明示パスで読み込む。

    scripts/lib は shell script も置く場所で Python パッケージにしたくないため、
    sys.path を汚さず importlib で読む(scripts/tests/test_sta_no_report.py と同方式)。
    重複検出は書き直さず、この1箇所で再利用する。
    """
    path = Path(__file__).resolve().parents[1] / "scripts" / "lib" / "sta_no_report.py"
    spec = importlib.util.spec_from_file_location("sta_no_report", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sta_no_report = _load_sta_no_report()

STATIC = Path(__file__).resolve().parent / "static"
BIND_HOST = "127.0.0.1"
BIND_PORT = 8090


def _read_inventory() -> list[str]:
    p = provision.INVENTORY
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.split("#", 1)[0].strip()
        if s:
            out.append(s)
    return out


def build_fleet_view(
    *,
    neighbors: list,
    inventory_ips: dict[str, str | None],
    statuses: dict[str, ChildStatus],
    known_macs: dict[str, str] | None = None,
    hub: str = "",
) -> dict:
    """画面へ渡すJSONを組み立てる純関数。

    known_macs: 過去に確認済みの entry->mac(TOFU)。省略時はIPのみでの
    突き合わせ(後方互換)。新たに信頼すべき組は戻り値の "_newly_trusted_macs"
    に入れる。ファイルへの永続化はこの関数の責務ではなく、_gather() が行う
    (この関数を副作用フリーな純関数のまま保つため)。
    """
    known_macs = known_macs or {}
    rows = classify(neighbors, inventory_ips, known_macs)
    children = []
    per_host: dict[str, dict] = {}
    for r in rows:
        if r.kind == "new":
            continue
        st = statuses.get(r.ip) if r.ip else None
        name = (st.hostname if st else None) or r.entry
        children.append({
            "mac": r.mac, "ip": r.ip, "entry": r.entry, "kind": r.kind,
            "hostname": st.hostname if st else None,
            "picamera": st.picamera if st else None,
            "web_server": st.web_server if st else None,
            "model": st.model if st else None,
            "ready": st.ready if st else None,
            "id_names": st.id_names if st else {},
            "error": st.error if st else None,
        })
        if st and st.id_names:
            per_host[name or (r.ip or "?")] = st.id_names

    dups = sta_no_report.find_duplicate_stations(per_host)
    known_names = [c["hostname"] or c["entry"] or "" for c in children]

    # unresolved が1つでもある間、discovery は残りを new ではなく unverified にする
    # (稼働中の子がIPドリフトでそこに現れうるため)。理由を画面へ伝える。
    blocked = [r.entry for r in rows if r.kind == "unresolved"]
    return {
        "children": children,
        "candidates": [
            {"mac": r.mac, "ip": r.ip} for r in rows if r.kind == "new"
        ],
        "blocked_reason": (
            "対応付けできない子があるため、新機の登録を止めています: "
            + ", ".join(str(b) for b in blocked)
            + "。先に既存の子を復旧するか、インベントリから外してください。"
        ) if blocked else None,
        "duplicates": [
            {"sta_no": list(t), "where": labels} for t, labels in dups.items()
        ],
        "suggested_hostname": suggest_hostname([n for n in known_names if n], hub=hub),
        "steps": [{"id": i, "label": lbl} for i, lbl in provision.STEPS],
        "_newly_trusted_macs": learned_macs(rows, known_macs),
    }


def enrich_candidates(view: dict, *, prober=None) -> dict:
    """未登録端末に、このハブの鍵で SSH できるかを足す。

    できるならホスト名と局番号を残して取り込める。できないなら子SDへの
    書き込みか、旧親経由の引き継ぎが先。
    """
    probe = prober or provision.probe_hostname
    for c in view.get("candidates") or []:
        ip = c.get("ip") or ""
        host = probe(ip) if ip else ""
        c["ssh_ok"] = bool(host)
        if host:
            c["hostname"] = host
    return view


def _gather() -> dict:
    entries = _read_inventory()
    inv = resolve_inventory_ips(entries)
    neighbors = read_neighbors()
    statuses = {ip: collect_status(ip) for ip in inv.values() if ip}
    known = load_known_macs()
    hub = read_hub_hostname(Path(__file__).resolve().parents[1] / "site.env")
    view = build_fleet_view(
        neighbors=neighbors, inventory_ips=inv, statuses=statuses, known_macs=known,
        hub=hub,
    )
    # TOFU: 今回新たに信頼した組があれば永続化する。フロントには内部実装の
    # 詳細を渡さないため、送信前に取り除く。
    newly = view.pop("_newly_trusted_macs", {})
    if newly:
        save_known_macs({**known, **newly})
    view["migrate"] = migrate.migrate_status()
    enrich_candidates(view)
    return view


# ブラウザからの CSRF を防ぐための独自ヘッダ。単純リクエストでは付けられず、
# 付けようとすると preflight が走る。こちらは CORS ヘッダを返さないので preflight は
# 失敗する。localhost に bind していても、操作者が開いた別サイトから
# /api/step を叩かれる経路は塞いでおく。
CSRF_HEADER = "X-Fleet-UI"

# DNS rebinding 対策。127.0.0.1 に bind していても、攻撃者のドメインを後から
# 127.0.0.1 へ解決させれば、ブラウザはそれを同一オリジンとみなし CSRF ヘッダを
# 自由に付けられてしまう。Host ヘッダが想定どおりかを別途確認する。
# ポートは self.server(実際に bind したサーバ)から取る。BIND_PORT を固定で
# 埋め込むと、テストが衝突を避けて別ポートで起動したときに常に弾かれてしまう。


def run_step(req: dict) -> dict:
    """登録ウィザードの1工程を実行する。

    hostname は ssh-keyscan の argv や children.conf の中身になるため、
    分岐に入る *前* にまとめて検証する。rename にだけ検証を置くと
    hostkey / inventory から素通りしてしまう。
    """
    step = req.get("step")
    ip = req.get("ip") or ""
    mac = req.get("mac") or ""
    new_hostname = req.get("hostname") or ""

    needs_hostname = step in ("rename", "hostkey", "inventory")
    if needs_hostname:
        err = validate_hostname(new_hostname, _read_inventory())
        if err:
            return {"ok": False, "message": err}

    # 登録ウィザードはインベントリに載っていない端末しか受け付けない。
    # 稼働中の子を誤って再プロビジョニングしないため。
    inv = resolve_inventory_ips(_read_inventory())
    if ip in {v for v in inv.values() if v}:
        return {"ok": False, "message": "この端末は既にインベントリに登録されています"}

    if step == "stop":
        r = provision.stop_publisher(ip)
    elif step == "blank":
        r = provision.blank_sta_no(ip)
    elif step == "rename":
        r = provision.rename_and_reboot(ip, new_hostname)
        if r.ok:
            w = provision.wait_for_return(mac)
            return {"ok": w.ok, "message": f"{r.message} / {w.message}",
                    "output": w.output}
    elif step == "mdns":
        targets = [v for v in inv.values() if v] + ([ip] if ip else [])
        r = provision.restart_mdns(targets)
    elif step == "hostkey":
        r = provision.register_host_key(f"{new_hostname}.local")
    elif step == "inventory":
        r = provision.add_to_inventory(f"{new_hostname}.local")
    else:
        return {"ok": False, "message": f"不明な工程: {step}"}
    return {"ok": r.ok, "message": r.message, "output": r.output}


def run_adopt(req: dict) -> dict:
    """AP 上の既存の子を、名前と局番号を変えずに取り込む。"""
    ip = req.get("ip") or ""
    inv = resolve_inventory_ips(_read_inventory())
    if ip in {v for v in inv.values() if v}:
        return {"ok": False, "message": "この端末は既にインベントリに登録されています"}
    r = provision.adopt_keeping_identity(ip, existing=_read_inventory())
    return {"ok": r.ok, "message": r.message, "output": r.output}


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _host_allowed(self) -> bool:
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}", "127.0.0.1", "localhost"}
        return (self.headers.get("Host") or "").strip() in allowed

    def do_GET(self):  # noqa: N802
        if not self._host_allowed():
            self._json({"error": "許可されていないHostヘッダです"}, 403)
            return
        if self.path in ("/", "/index.html"):
            body = (STATIC / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/fleet":
            self._json(_gather())
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        if not self._host_allowed():
            self._json({"ok": False, "message": "許可されていないHostヘッダです"}, 403)
            return
        # 独自ヘッダが無い POST は受け付けない(CSRF対策)。
        if self.headers.get(CSRF_HEADER) != "1":
            self._json({"ok": False, "message": f"{CSRF_HEADER} ヘッダが必要です"}, 403)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json({"ok": False, "message": "リクエストを解析できません"}, 400)
            return
        if self.path == "/api/step":
            self._json(run_step(req))
            return
        if self.path == "/api/migrate/list":
            self._json(migrate.list_children_payload(req.get("old_host") or ""))
            return
        if self.path == "/api/migrate/take":
            self._json(migrate.take_payload(
                req.get("old_host") or "",
                req.get("entry") or "",
            ))
            return
        if self.path == "/api/adopt":
            self._json(run_adopt(req))
            return
        self._json({"ok": False, "error": "not found"}, 404)

    def log_message(self, fmt, *args):
        print(f"[fleet-ui] {fmt % args}")


def make_server(host: str = BIND_HOST, port: int = BIND_PORT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


def main() -> int:
    srv = make_server()
    print(f"[fleet-ui] http://{BIND_HOST}:{BIND_PORT} で待ち受けます")
    print("[fleet-ui] 操作者は ssh -L 8090:localhost:8090 pi@<親> で接続してください")
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
