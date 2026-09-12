"""ターミナルから子の付け替えを呼び出す。

フリート管理の HTTP は使わない。SSH とインベントリの操作は migrate / provision に任せる。
PSK は標準出力へ出さない。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fleet_ui import migrate, provision
from fleet_ui.discovery import read_neighbors, resolve_inventory_ips

REPO = Path(__file__).resolve().parents[1]
INVENTORY = REPO / "fleet" / "children.conf"


def _emit(obj: dict) -> int:
    json.dump(obj, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0 if obj.get("ok", True) else 1


def _inventory_entries() -> list[str]:
    if not INVENTORY.exists():
        return []
    out = []
    for line in INVENTORY.read_text(encoding="utf-8").splitlines():
        s = line.split("#", 1)[0].strip()
        if s:
            out.append(s)
    return out


def cmd_status(_args: list[str]) -> int:
    st = migrate.migrate_status(repo=REPO)
    return _emit({"ok": True, **st})


def cmd_pubkey(_args: list[str]) -> int:
    key = migrate.read_pubkey()
    return _emit({"ok": bool(key), "pubkey": key})


def cmd_list(args: list[str]) -> int:
    if not args:
        return _emit({"ok": False, "message": "旧親のアドレスを指定してください", "children": []})
    return _emit(migrate.list_children_payload(args[0]))


def cmd_take(args: list[str]) -> int:
    if len(args) < 2:
        return _emit({"ok": False, "message": "旧親と子の名前を指定してください"})
    return _emit(migrate.take_payload(args[0], args[1], repo=REPO))


def cmd_candidates(_args: list[str]) -> int:
    entries = _inventory_entries()
    inv_ips = {ip for ip in resolve_inventory_ips(entries).values() if ip}
    rows = []
    for n in read_neighbors():
        if n.ip in inv_ips:
            continue
        host = provision.probe_hostname(n.ip)
        rows.append({
            "mac": n.mac,
            "ip": n.ip,
            "ssh_ok": bool(host),
            "hostname": host or None,
        })
    return _emit({"ok": True, "candidates": rows})


def cmd_adopt(args: list[str]) -> int:
    if not args:
        return _emit({"ok": False, "message": "子の IP を指定してください"})
    r = provision.adopt_keeping_identity(
        args[0], existing=_inventory_entries(), inventory_path=INVENTORY
    )
    return _emit({"ok": r.ok, "message": r.message, "output": r.output})


COMMANDS = {
    "status": cmd_status,
    "pubkey": cmd_pubkey,
    "list": cmd_list,
    "take": cmd_take,
    "candidates": cmd_candidates,
    "adopt": cmd_adopt,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(
            "usage: python3 -m fleet_ui.child_cli "
            "{status|pubkey|list|take|candidates|adopt} ...",
            file=sys.stderr,
        )
        return 2
    cmd = argv[0]
    fn = COMMANDS.get(cmd)
    if fn is None:
        return _emit({"ok": False, "message": f"不明なコマンド: {cmd}"})
    return fn(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
