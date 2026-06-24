#!/usr/bin/env python3
"""show-recent-records.sh 用の整形フィルタ。

oracle-jdbc サイドカーの /select_recent 応答（key=value テキスト）を
標準入力で受け取り、人が読める一覧表に整形する。

応答フォーマット:
    count=N
    ora_code=        (空 or ORA番号)
    error_message=
    row=MK_DATE,STA_NO1,STA_NO2,STA_NO3,T1_STATUS,UPCMPFLG
    ...(最新順 / DESC)

翻訳:
    MK_DATE  YYYYMMDDHHMMSS -> "YYYY-MM-DD HH:MM:SS"
    T1_STATUS 1 -> 🟢ENTER / 2 -> 🔴EXIT
"""
import sys

STATUS = {"1": "🟢ENTER", "2": "🔴EXIT "}


def fmt_mk(mk: str) -> str:
    # 14桁 YYYYMMDDHHMMSS を素直に整形。桁が違えば生値のまま返す（壊さない）。
    if len(mk) == 14 and mk.isdigit():
        return f"{mk[0:4]}-{mk[4:6]}-{mk[6:8]} {mk[8:10]}:{mk[10:12]}:{mk[12:14]}"
    return mk


def main() -> int:
    count = None
    ora_code = ""
    error_message = ""
    rows = []
    for raw in sys.stdin:
        line = raw.rstrip("\n")
        if line.startswith("count="):
            count = line[len("count="):]
        elif line.startswith("ora_code="):
            ora_code = line[len("ora_code="):]
        elif line.startswith("error_message="):
            error_message = line[len("error_message="):]
        elif line.startswith("row="):
            rows.append(line[len("row="):].split(",", 5))

    if ora_code or error_message:
        print()
        print("  ❌ DB照会でエラーが発生しました")
        if ora_code:
            print(f"     ORA-{ora_code}")
        if error_message:
            print(f"     {error_message}")
        print()
        print("  ヒント: taden-ot-ap に接続中か確認してください（未接続だとDBに届きません）。")
        return 1

    if not rows:
        print()
        print("  📭 該当する記録がまだありません（この STA_NO の行は0件）。")
        print("     人を検知してDB書込が起きると、ここに最新順で表示されます。")
        print()
        return 0

    # 列幅をそろえてテーブル表示（最新順 = サイドカーの DESC をそのまま）。
    print()
    print(f"  {'#':>3}  {'日時 (JST)':<19}  {'種別':<7}  {'STA(1/2/3)':<14}  UPCMPFLG")
    print(f"  {'-'*3}  {'-'*19}  {'-'*7}  {'-'*14}  {'-'*8}")
    for i, r in enumerate(rows, 1):
        mk, s1, s2, s3, t1 = r[0], r[1], r[2], r[3], r[4]
        upc = r[5] if len(r) > 5 else ""
        sta = f"{s1}/{s2}/{s3}"
        badge = STATUS.get(t1, f"?({t1})")
        print(f"  {i:>3}  {fmt_mk(mk):<19}  {badge:<7}  {sta:<14}  {upc}")
    print()
    print(f"  合計 {count} 件（最新が上）。 凡例: 🟢ENTER=入室 / 🔴EXIT=退室")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
