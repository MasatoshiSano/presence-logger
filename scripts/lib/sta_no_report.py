#!/usr/bin/env python3
# ruff: noqa: T201  これは操作用CLIなので print 出力は意図的
"""子ごとの STA_NO 割当を突き合わせ、重複を検出する。

Oracle の MERGE キーは (MK_DATE, STA_NO1..3, T1_STATUS) のみで device_id を含まない
（services/bridge/src/oracle_client.py）。よって複数の子が同じ STA_NO 三つ組を持つと、
同時刻・同ステータスのレコードは片方が無警告で捨てられる。子を増やす前・増やした後に
このチェックを通すことで、その事故を運用前に検出する。

標準入力: {"<host>": {"<region_id>": ["名前1", "名前2", "名前3"], ...}, ...}
終了コード: 重複があれば 1、無ければ 0。
"""
import json
import sys

Assignments = dict[str, dict[str, list[str]]]
Triple = tuple[str, str, str]


def _triple(parts: list[str]) -> Triple:
    """3要素へ正規化する（欠けは空文字で埋める）。"""
    p = [str(x).strip() for x in (parts or [])][:3]
    while len(p) < 3:
        p.append("")
    return (p[0], p[1], p[2])


def find_duplicate_stations(per_host: Assignments) -> dict[Triple, list[str]]:
    """重複した STA_NO 三つ組 -> ["<host>:<region_id>", ...] を返す。

    3項目すべて空の region は未割当として無視する（Picamera.py の any(parts) と同じ判定）。
    同一ホスト内での重複も Oracle では衝突するため検出対象に含める。
    """
    seen: dict[Triple, list[str]] = {}
    for host in sorted(per_host):
        for region in sorted(per_host[host], key=lambda r: (len(r), r)):
            triple = _triple(per_host[host][region])
            if not any(triple):
                continue
            seen.setdefault(triple, []).append(f"{host}:{region}")
    return {t: labels for t, labels in seen.items() if len(labels) > 1}


def format_report(per_host: Assignments) -> str:
    """全機体の STA_NO 割当一覧を人が読める形にする。"""
    lines: list[str] = []
    for host in sorted(per_host):
        regions = per_host[host] or {}
        lines.append(f"[{host}]")
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
    print(format_report(per_host))
    dups = find_duplicate_stations(per_host)
    if not dups:
        print("\nSTA_NO 重複: なし")
        return 0
    print("\n!! STA_NO 重複を検出 !!")
    for triple, labels in dups.items():
        print(f"    {triple[0]} / {triple[1]} / {triple[2]}  <- {', '.join(labels)}")
    print("\nOracle の MERGE キーは device_id を含まないため、このままでは")
    print("同時刻・同ステータスのレコードが無警告で欠落します。割当を修正してください。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
