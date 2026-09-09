#!/usr/bin/env python3
"""show-recent-records.sh 用の整形フィルタ。

oracle-jdbc サイドカーの /select_recent 応答（key=value テキスト）を
人が読める一覧表に整形する。

応答フォーマット:
    count=N
    ora_code=        (空 or ORA番号)
    error_message=
    row=MK_DATE,STA_NO1,STA_NO2,STA_NO3,T1_STATUS,UPCMPFLG
    ...(最新順 / DESC)

T1_STATUS は入退室(1/2)に限らない任意コードなので、ENTER/EXIT へ翻訳せず
数値そのまま表示する。
"""
import sys


def fmt_mk(mk: str) -> str:
    # 14桁 YYYYMMDDHHMMSS を素直に整形。桁が違えば生値のまま返す（壊さない）。
    if len(mk) == 14 and mk.isdigit():
        return f"{mk[0:4]}-{mk[4:6]}-{mk[6:8]} {mk[8:10]}:{mk[10:12]}:{mk[12:14]}"
    return mk


def render(text: str) -> str:
    count = None
    ora_code = ""
    error_message = ""
    rows = []
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if line.startswith("count="):
            count = line[len("count="):]
        elif line.startswith("ora_code="):
            ora_code = line[len("ora_code="):]
        elif line.startswith("error_message="):
            error_message = line[len("error_message="):]
        elif line.startswith("row="):
            rows.append(line[len("row="):].split(",", 5))

    out = []
    if ora_code or error_message:
        out.append("")
        out.append("  ❌ DB照会でエラーが発生しました")
        if ora_code:
            out.append(f"     ORA-{ora_code}")
        if error_message:
            out.append(f"     {error_message}")
        out.append("")
        out.append("  ヒント: HIME-H-REAP に接続中か確認してください（未接続だとDBに届きません）。")
        return "\n".join(out)

    if not rows:
        out.append("")
        out.append("  📭 該当する記録がありません（この条件の行は0件）。")
        out.append("")
        return "\n".join(out)

    out.append("")
    out.append(f"  {'#':>3}  {'日時 (JST)':<19}  {'T1':>4}  {'STA(1/2/3)':<16}  UPCMPFLG")
    out.append(f"  {'-'*3}  {'-'*19}  {'-'*4}  {'-'*16}  {'-'*8}")
    for i, r in enumerate(rows, 1):
        mk, s1, s2, s3, t1 = r[0], r[1], r[2], r[3], r[4]
        upc = r[5] if len(r) > 5 else ""
        sta = f"{s1}/{s2}/{s3}"
        out.append(f"  {i:>3}  {fmt_mk(mk):<19}  {t1:>4}  {sta:<16}  {upc}")
    out.append("")
    out.append(f"  合計 {count} 件（最新が上）。 T1=T1_STATUS(数値そのまま)")
    out.append("")
    return "\n".join(out)


def main() -> int:
    print(render(sys.stdin.read()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
