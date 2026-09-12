"""ターミナルから子の付け替えを呼び出す。

フリート管理の HTTP は使わない。SSH とインベントリの操作は migrate / provision に任せる。
PSK は標準出力へ出さない。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fleet_ui import migrate, provision
from fleet_ui.hostname import suggest_hostname, validate_hostname
from fleet_ui.discovery import (
    classify,
    load_known_macs,
    read_neighbors,
    resolve_inventory_ips,
)

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
    inventory_ips = resolve_inventory_ips(entries)
    known = load_known_macs()
    rows = []
    for r in classify(read_neighbors(), inventory_ips, known):
        if r.kind != "new":
            continue
        host = provision.probe_hostname(r.ip) if r.ip else ""
        rows.append({
            "mac": r.mac,
            "ip": r.ip,
            "ssh_ok": bool(host),
            "hostname": host or None,
            "kind": r.kind,
        })
    return _emit({"ok": True, "candidates": rows})


def cmd_suggest(_args: list[str]) -> int:
    return _emit({"ok": True, "hostname": suggest_hostname(_inventory_entries())})


def cmd_register(args: list[str]) -> int:
    if len(args) < 3:
        return _emit({"ok": False, "message": "IP と MAC と新しいホスト名を指定してください"})
    err = validate_hostname(args[2], _inventory_entries())
    if err:
        return _emit({"ok": False, "message": err})
    r = provision.register_new_child(
        args[0],
        args[1],
        args[2],
        existing=_inventory_entries(),
        inventory_path=INVENTORY,
    )
    return _emit({"ok": r.ok, "message": r.message, "output": r.output})


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
    "suggest": cmd_suggest,
    "register": cmd_register,
    "adopt": cmd_adopt,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(
            "usage: python3 -m fleet_ui.child_cli "
            "{status|pubkey|list|take|candidates|suggest|register|adopt} ...",
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
