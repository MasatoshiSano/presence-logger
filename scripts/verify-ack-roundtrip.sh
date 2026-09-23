#!/usr/bin/env bash
# verify-ack-roundtrip.sh — 子が送った記録が Oracle に入り、その ACK を
# 子が受け取るところまでを、工場網に繋いだ状態で確かめる。
#
#   bash scripts/verify-ack-roundtrip.sh [観測秒数]
#
# 「Oracle に入った」と「子がそれを知った」は別物。前者だけ見て成功と呼ぶと、
# 2026-09-23 に見つけた穴(Oracle へ届かなかった 897 件を、子が送信済みとして
# 保持していた)をまた見逃す。この検証は必ず両方を別々に数える。
#
# 読み取りのみ。設定も記録も書き換えない。
set -uo pipefail

VA_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WATCH_S="${1:-120}"
ACK_TOPIC="${ACK_TOPIC:-presence/record/ack}"
BUF=/var/lib/presence-logger/bridge_record_buf.db

say() { printf '%s\n' "$*"; }

# 3層(Oracleコミット / ACK発行 / 子の受領)が食い違っていないかを判定する。
# 引数は key=value。テストから直接呼べるように副作用を持たせない。
ack_verdict() {
    local committed=0 acks=0 child=0 kv
    for kv in "$@"; do
        case "$kv" in
            committed=*)       committed="${kv#*=}" ;;
            acks_published=*)  acks="${kv#*=}" ;;
            child_acked=*)     child="${kv#*=}" ;;
        esac
    done

    if [ "$committed" -eq 0 ] && [ "$acks" -gt 0 ]; then
        say "❌ 矛盾: Oracle に1件も入っていないのに ACK が ${acks} 件出ています。"
        say "   嘘の ACK を返しています。子は入っていない記録を完了にします。"
        return 1
    fi
    if [ "$committed" -eq 0 ] && [ "$acks" -eq 0 ] && [ "$child" -eq 0 ]; then
        say "― 判定できません: 観測中に1件も流れませんでした。"
        say "   子が送るのを待つか、観測秒数を延ばしてやり直してください。"
        return 2
    fi
    if [ "$acks" -lt "$committed" ]; then
        say "❌ Oracle に ${committed} 件入りましたが ACK は ${acks} 件しか出ていません。"
        say "   差の $((committed - acks)) 件は、子が完了を知る手段がありません。"
        return 1
    fi
    if [ "$child" -lt "$acks" ]; then
        say "❌ ACK は ${acks} 件出ましたが、子が受領したのは ${child} 件です。"
        say "   子が ACK を購読していないか、取りこぼしています。"
        say "   → これが本検証の目的そのものです。子側の購読を確認してください。"
        return 1
    fi
    say "✅ PASS: Oracle ${committed} 件 → ACK ${acks} 件 → 子の受領 ${child} 件。"
    say "   3層とも一致しました。"
    return 0
}

va_counts() {  # status ごとの件数を "status=n" で出す
    docker exec presence-bridge python3 -c "
import sqlite3
c=sqlite3.connect('file:$BUF?mode=ro',uri=True)
for s,n in c.execute('select status,count(*) from record_inbox group by status'):
    print(f'{s}={n}')
" 2>/dev/null
}

va_get() { printf '%s\n' "$1" | grep -E "^$2=" | cut -d= -f2 | head -1; }

va_precheck() {
    local ok=0
    say "== 前提の確認 =="
    printf '  工場網(SSID)   : '
    nmcli -t -f ACTIVE,SSID dev wifi 2>/dev/null | grep '^yes' | cut -d: -f2 | tr '\n' ' '; echo
    printf '  Oracle 到達    : '
    if docker exec presence-oracle-jdbc sh -lc 'nc -z -w3 10.166.5.93 1521' 2>/dev/null; then
        echo "✅"
    else
        echo "❌ 未到達 — 工場網に繋いでから実行してください"; ok=1
    fi
    printf '  ブリッジ       : %s\n' "$(docker inspect -f '{{.State.Health.Status}}' presence-bridge 2>/dev/null)"
    printf '  子の接続       : %s 台\n' "$(iw dev wlan1 station dump 2>/dev/null | grep -c '^Station')"
    return "$ok"
}

# 子が ACK を購読しているか。ブローカーのログに子の SUBSCRIBE が出る。
va_child_subscribed() {
    docker logs presence-mosquitto --since 24h 2>&1 \
        | grep -ciE "received SUBSCRIBE from .*child|$ACK_TOPIC" || true
}

main() {
    va_precheck || return 1
    echo

    say "== 観測開始（${WATCH_S}秒） =="
    local before after
    before="$(va_counts)"
    say "  開始時: $(printf '%s' "$before" | tr '\n' ' ')"

    local acklog; acklog="$(mktemp)"
    docker exec presence-mosquitto mosquitto_sub -h 127.0.0.1 \
        -t "$ACK_TOPIC" -t "${ACK_TOPIC}/#" -v -W "$WATCH_S" > "$acklog" 2>/dev/null || true

    after="$(va_counts)"
    say "  終了時: $(printf '%s' "$after" | tr '\n' ' ')"
    echo

    local sb sa committed acks child
    sb="$(va_get "$before" sent)"; sa="$(va_get "$after" sent)"
    committed=$(( ${sa:-0} - ${sb:-0} ))
    [ "$committed" -lt 0 ] && committed=0
    acks="$(grep -c . "$acklog" 2>/dev/null || echo 0)"
    child="$(va_child_subscribed)"

    say "== 結果 =="
    say "  Oracle に入った : ${committed} 件 (sent の増分)"
    say "  ACK が出た      : ${acks} 件 ($ACK_TOPIC)"
    say "  子が受領した    : ${child} 件相当 (ブローカーの購読記録)"
    echo
    ack_verdict "committed=$committed" "acks_published=$acks" "child_acked=$child"
    local rc=$?
    rm -f "$acklog"

    echo
    say "※ 「子が受領した」はブローカー側から見た購読の有無です。子の台帳を"
    say "   直接見るには、子側の ACK 受信実装が入ったあとで再実行してください。"
    return "$rc"
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
