#!/usr/bin/env bash
# watch-records.sh
# 接続中に「実際に何が HHC001 へ書き込まれているか」をリアルタイム表示する。
#   detector(ENTER/EXIT検知) と bridge(DB書込) を1画面に流す。
#   event_id を突き合わせ、DB書込の行にも 🟢ENTER / 🔴EXIT を表示する。
#   1行 = 1イベント。✅DB書込(NEW) が Oracle に実際に入った行。終了は Ctrl-C。
#
# 使い方:  bash watch-records.sh [SINCE=30s]
set -uo pipefail

HUB_MODE="${HUB_MODE:-0}"

watch_containers() {
    [ "$HUB_MODE" = "1" ] || printf 'presence-detector\n'
    printf 'presence-bridge\n'
}

main() {
    # site.env があれば読む(HUB_MODE / HOME_SSID / PROFILE_NAME 等)。
    # 無い機体でも従来どおり動くよう、失敗は無視する。
    _SITE_ENV="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/site.env"
    # shellcheck disable=SC1090
    [ -f "$_SITE_ENV" ] && { set -a; source "$_SITE_ENV"; set +a; }
    HUB_MODE="${HUB_MODE:-0}"

    DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    export SINCE="${1:-30s}"   # 最初に何分/秒前から表示するか（その後はリアルタイム追従）

    # wlan0 固定で見る（wlan1 は常時 presence-hub AP なので nmcli の先頭yes行は誤検出）。
    SSID="$(iwgetid -r "${WAN_IFACE:-wlan0}" 2>/dev/null)"
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
    # 購読対象は watch_containers の出力。docker logs 行にコンテナ名を埋め込まない。
    FIFO="$(mktemp -u /tmp/presmon.XXXXXX)"
    mkfifo "$FIFO"

    CONTAINERS="$(watch_containers)"
    export CONTAINERS
    setsid bash -c '
      {
        while IFS= read -r c; do
          [ -n "$c" ] || continue
          docker logs --since "$SINCE" "$c" 2>&1
        done <<< "$CONTAINERS"
      } | LC_ALL=C sort
      while IFS= read -r c; do
        [ -n "$c" ] || continue
        docker logs -f --since 0s "$c" 2>&1 &
      done <<< "$CONTAINERS"
      wait
    ' >"$FIFO" &
    WP=$!

    cleanup(){ kill -- -"$WP" 2>/dev/null || true; rm -f "$FIFO"; }
    trap cleanup EXIT INT TERM

    python3 -u "$DIR/_render.py" <"$FIFO"
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
