#!/usr/bin/env python3
# ruff: noqa: T201  これは操作用CLIなので print 出力は意図的
"""子ごとの STA_NO 割当を突き合わせ、重複を検出する。

Oracle の MERGE キーは (MK_DATE, STA_NO1..3, T1_STATUS) のみで device_id を含まない
（services/bridge/src/oracle_client.py）。よって複数の子が同じ STA_NO 三つ組を持つと、
同時刻・同ステータスのレコードは片方が無警告で捨てられる。子を増やす前・増やした後に
このチェックを通すことで、その事故を運用前に検出する。

標準入力: {"<host>": {"<region_id>": ["名前1", "名前2", "名前3"], ...}, ...}
終了コード: 0 = 検査済みで重複なし / 1 = 重複あり / 2 = 検査しきれない(最も実行可能な合図1を優先)
"""
import json
import sys

Assignments = dict[str, dict[str, list[str]]]
Triple = tuple[str, str, str]


def _triple(parts: object) -> Triple:
    """3要素へ正規化する。子側 child/Picamera.py の id_name_parts と同じ規則に揃える。

    - リスト以外(旧形式の単一文字列)は ["旧名", "", ""] とみなす。文字単位に分解しない。
    - None は空文字にする(str(None) の "None" にしてはいけない)。
    - 前後の空白を除去する(子が strip してから CSV に書くため)。
    3要素に満たない場合は空文字で埋める。

    ここが子の正規化とずれると、同じ局を指す2台の子を「別物」と誤判定し、
    Oracle でレコードが無警告に欠落するのを見逃す。
    """
    if isinstance(parts, list):
        p = [("" if x is None else str(x)).strip() for x in parts][:3]
    else:
        p = ["" if parts is None else str(parts).strip()]
    while len(p) < 3:
        p.append("")
    return (p[0], p[1], p[2])


def find_malformed_hosts(per_host: Assignments) -> list[str]:
    """割当が dict になっておらず検査できないホスト名を返す。"""
    return sorted(h for h in per_host if not isinstance(per_host[h], dict))


def find_duplicate_stations(per_host: Assignments) -> dict[Triple, list[str]]:
    """重複した STA_NO 三つ組 -> ["<host>:<region_id>", ...] を返す。

    3項目すべて空の region は未割当として無視する（Picamera.py の any(parts) と同じ判定）。
    同一ホスト内での重複も Oracle では衝突するため検出対象に含める。
    """
    seen: dict[Triple, list[str]] = {}
    for host in sorted(per_host):
        regions = per_host[host]
        if not isinstance(regions, dict):
            continue  # 検査不能。main() が別途警告する。
        for region in sorted(regions, key=lambda r: (len(r), r)):
            triple = _triple(regions[region])
            if not any(triple):
                continue
            seen.setdefault(triple, []).append(f"{host}:{region}")
    return {t: labels for t, labels in seen.items() if len(labels) > 1}


def format_report(per_host: Assignments) -> str:
    """全機体の STA_NO 割当一覧を人が読める形にする。"""
    lines: list[str] = []
    for host in sorted(per_host):
        regions = per_host[host]
        lines.append(f"[{host}]")
        if not isinstance(regions, dict):
            lines.append("    (割当を読めませんでした)")
            continue
        assigned = [
            (r, _triple(regions[r]))
            for r in sorted(regions, key=lambda r: (len(r), r))
            if any(_triple(regions[r]))
        ]
        if not assigned:
            lines.append("    (STA_NO 未割当)")
            continue
        for region, triple in assigned:
            lines.append(f"    region {region:>3}  {triple[0]} / {triple[1]} / {triple[2]}")
    return "\n".join(lines)


def main() -> int:
    try:
        per_host: Assignments = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"入力JSONを解析できません: {e}", file=sys.stderr)
        return 2
    if not isinstance(per_host, dict):
        print("入力JSONの最上位は {ホスト名: 割当} である必要があります", file=sys.stderr)
        return 2
    print(format_report(per_host))
    bad = find_malformed_hosts(per_host)
    dups = find_duplicate_stations(per_host)
    if bad:
        print("\n!! 割当を読めなかった子があります(この子は検査できていません) !!")
        for h in bad:
            print(f"    {h}")
    if dups:
        print("\n!! STA_NO 重複を検出 !!")
        for triple, labels in dups.items():
            print(f"    {triple[0]} / {triple[1]} / {triple[2]}  <- {', '.join(labels)}")
        print("\nOracle の MERGE キーは device_id を含まないため、このままでは")
        print("同時刻・同ステータスのレコードが無警告で欠落します。割当を修正してください。")
        return 1
    if bad:
        print("\n上記の子を検査できていないため、重複なしとは断定できません。")
        return 2
    print("\nSTA_NO 重複: なし")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
