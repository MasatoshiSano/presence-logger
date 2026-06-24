#!/usr/bin/env python3
"""watch-records.sh 用の整形フィルタ。

detector(presence-detector) と bridge(presence-bridge) の JSON ログを
1本のストリームで受け取り、event_id をキーに「その書き込みが ENTER か EXIT か」を
突き合わせて表示する。bridge の merge_committed ログ自体には種別が無いため、
detector の transition から作った対応表 (typ) を参照して復元する。
"""
import json
import sys

# 種別バッジ（ENTER/EXIT を5文字幅に揃える）
EMO = {"ENTER": "🟢ENTER", "EXIT": "🔴EXIT "}
typ: dict[str, str] = {}   # event_id -> "ENTER"/"EXIT"


def badge(eid: str) -> str:
    return EMO.get(typ.get(eid, ""), "  ?  ")


def short(eid) -> str:
    return (eid or "")[:8]


for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    try:
        rec = json.loads(raw)
    except Exception:
        continue
    ev = rec.get("event")
    eid = rec.get("event_id")
    ts = rec.get("ts", "")

    # 種別を持つ行（detector の transition だけでなく、bridge の received /
    # merge_committed / merge_failed も event_type を載せる）は対応表を更新する。
    # これで起動時バッファ分や detector 行が表示窓の外でも ? にならない。
    et = rec.get("event_type")
    if eid and et:
        typ[eid] = et

    if ev == "transition":
        t = rec.get("event_type")
        if eid:
            typ[eid] = t
        print(f"{ts}  {EMO.get(t, t or '?')} 検知              id={short(eid)}", flush=True)
    elif ev == "received":
        print(f"{ts}  📥 受信 [{badge(eid)}]            id={short(eid)}", flush=True)
    elif ev == "merge_committed":
        rows = rec.get("rows_affected") or 0
        tag = "✅ DB書込(NEW)" if rows > 0 else "➖ 重複skip   "
        print(f"{ts}  {tag} [{badge(eid)}] mk={rec.get('mk_date')} rows={rows} id={short(eid)}",
              flush=True)
    elif ev == "merge_failed":
        print(f"{ts}  ❌ 失敗 [{badge(eid)}] ora={rec.get('ora_code')} id={short(eid)}", flush=True)
