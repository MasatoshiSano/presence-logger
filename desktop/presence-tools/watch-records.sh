#!/usr/bin/env bash
# watch-records.sh
# 接続中に「実際に何が HHC001 へ書き込まれているか」をリアルタイム表示する。
#   detector(ENTER/EXIT検知) と bridge(DB書込) を1画面に流す。
#   event_id を突き合わせ、DB書込の行にも 🟢ENTER / 🔴EXIT を表示する。
#   1行 = 1イベント。✅DB書込(NEW) が Oracle に実際に入った行。終了は Ctrl-C。
#
# 使い方:  bash watch-records.sh [SINCE=30s]
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export SINCE="${1:-30s}"   # 最初に何分/秒前から表示するか（その後はリアルタイム追従）

SSID="$(nmcli -t -f ACTIVE,SSID dev wifi 2>/dev/null | awk -F: '$1=="yes"{print $2; exit}')"
echo "===================================================================="
echo " presence-logger 記録モニタ（リアルタイム）"
echo "   現在のSSID : ${SSID:-(不明)}"
echo "   凡例: 🟢ENTER 🔴EXIT / 📥受信 ✅DB書込(NEW) ➖重複skip ❌失敗"
echo "        [🟢/🔴] は その書き込みが 入室/退室 どちらかを表します"
echo "   ※SSIDが HIME-H-REAP のときだけ「DB書込」が出ます"
echo "     （他のSSIDでは未登録→drop されDBには行きません）"
echo "   終了: Ctrl-C"
echo "===================================================================="
echo

# detector と bridge を1ストリームにまとめ、_render.py で整形する。
#   - 過去分(backlog)は ts で sort してから流す → 検知行が書込行より先に処理され、
#     DB書込の行にも ENTER/EXIT バッジが正しく付く
#   - その後はライブ追従（実時間で検知が先行するので順序は保たれる）
# setsid で独立プロセスグループにし、終了時に全部まとめて kill する。
FIFO="$(mktemp -u /tmp/presmon.XXXXXX)"
mkfifo "$FIFO"

setsid bash -c '
  { docker logs --since "$SINCE" presence-detector 2>&1
    docker logs --since "$SINCE" presence-bridge   2>&1; } | LC_ALL=C sort
  docker logs -f --since 0s presence-detector 2>&1 &
  docker logs -f --since 0s presence-bridge   2>&1 &
  wait
' >"$FIFO" &
WP=$!

cleanup(){ kill -- -"$WP" 2>/dev/null || true; rm -f "$FIFO"; }
trap cleanup EXIT INT TERM

python3 -u "$DIR/_render.py" <"$FIFO"
