#!/usr/bin/env bash
# pack-hub-usb-gui.sh — デスクトップから1クリックで、新しいハブ用の USB を作る。
#
# pack-hub-usb.sh をそのまま呼ぶだけでは現場で3か所つまずく:
#   1. マウント先のパスを手で打たせることになる
#   2. secrets.env を読むのに sudo が要る(忘れると工場WiFiのPSKが載らない)
#   3. その sudo が HOME を /root へ差し替えるため、ドライバソースを探す
#      ${HOME}/8821au が見えなくなり、ドライバ無しのキットが「成功」で出来上がる
# ここはその3つを引き受ける層。中身の判断は pack-hub-usb.sh に任せる。
set -uo pipefail

GUI_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NEED_MB="${PACK_NEED_MB:-500}"   # キット実測 286MB + 余裕

say() { printf '%s\n' "$*"; }
die() { printf '%s\n' "$*" >&2; return 1; }

# /media/<ユーザ>/<ラベル> の深さで、書き込めるものだけを候補にする。
# /media/<ユーザ> 自身(ラベル無し)を掴むと pack がユーザのホーム相当に
# 書き込んでしまうので、必ず2段目を見る。
gui_find_usb_candidates() {
    local root="${1:-/media}" d
    for d in "$root"/*/*; do
        [ -d "$d" ] || continue
        [ -w "$d" ] || continue
        printf '%s\n' "$d"
    done
}

gui_free_mb() {
    df -Pm "$1" 2>/dev/null | awk 'NR==2 {print $4}'
}

# sudo が HOME を差し替えても効くように、ドライバの場所をここで確定させる。
gui_driver_src() {
    local user="${SUDO_USER:-$USER}" home
    home="$(getent passwd "$user" | cut -d: -f6)"
    if [ -n "${PACK_DRIVER_SRC-}" ]; then
        printf '%s\n' "$PACK_DRIVER_SRC"
    elif [ -d /usr/local/src/8821au ]; then
        printf '%s\n' /usr/local/src/8821au
    elif [ -n "$home" ] && [ -d "$home/8821au" ]; then
        printf '%s\n' "$home/8821au"
    fi
}

gui_pick_usb() {
    local -a c=()
    while IFS= read -r d; do c+=("$d"); done < <(gui_find_usb_candidates /media)
    while IFS= read -r d; do c+=("$d"); done < <(gui_find_usb_candidates /run/media)

    if [ "${#c[@]}" -eq 0 ]; then
        die "USB メモリが見つかりません。
  ・挿してからもう一度このアイコンを開いてください
  ・ファイルマネージャに中身が出ていれば認識されています"
        return 1
    fi
    if [ "${#c[@]}" -eq 1 ]; then
        printf '%s\n' "${c[0]}"
        return 0
    fi
    say "USB が複数あります。番号で選んでください:" >&2
    local i=1 d
    for d in "${c[@]}"; do
        printf '  %d) %s  (空き %s MB)\n' "$i" "$d" "$(gui_free_mb "$d")" >&2
        i=$((i + 1))
    done
    local n
    read -r -p "番号: " n
    case "$n" in
        ''|*[!0-9]*) die "番号で選んでください"; return 1 ;;
    esac
    [ "$n" -ge 1 ] && [ "$n" -le "${#c[@]}" ] || { die "範囲外です"; return 1; }
    printf '%s\n' "${c[$((n - 1))]}"
}

main() {
    local usb="${1:-}"
    say "新しいハブ用の USB キットを作ります。"
    say

    if [ -z "$usb" ]; then
        usb="$(gui_pick_usb)" || return 1
    fi
    [ -d "$usb" ] && [ -w "$usb" ] || { die "書き込めません: $usb"; return 1; }

    local free; free="$(gui_free_mb "$usb")"
    if [ -n "$free" ] && [ "$free" -lt "$NEED_MB" ]; then
        die "空きが足りません: $usb は ${free}MB（${NEED_MB}MB 以上必要）"
        return 1
    fi

    local drv; drv="$(gui_driver_src)"
    say "  書き込み先   : $usb  (空き ${free:-?}MB)"
    if [ -n "$drv" ]; then
        say "  ドライバ     : $drv"
    else
        say "  ドライバ     : 見つかりません（新機は起動時にネットから取得します）"
    fi
    say "  中身         : リポジトリ / ドライバ源 / コンテナ3本 / 工場WiFiのPSK"
    say "  載せないもの : 子の一覧・Oracleパスワード・APパスワード・機体固有値"
    say
    say "⚠ 出来た USB は鍵と同じ扱いにしてください（工場WiFi のパスワードが入ります）"
    say
    local yn
    read -r -p "この USB に書き込みますか？ [y/N]: " yn
    case "$yn" in y|Y) ;; *) say "中止しました"; return 1 ;; esac
    say

    # secrets.env は root しか読めない。ここで昇格し、HOME 差し替えの影響を
    # 受けないよう PACK_DRIVER_SRC を明示的に渡す。
    say "管理者権限が要ります（パスワードを聞かれます）"
    sudo -v || { die "sudo を通せませんでした"; return 1; }
    sudo PACK_DRIVER_SRC="$drv" bash "$GUI_REPO/scripts/pack-hub-usb.sh" "$usb"
    local rc=$?

    say
    if [ "$rc" -eq 0 ]; then
        local kit="$usb/presence-hub-kit"
        say "── できあがり ──"
        du -sh "$kit" 2>/dev/null | awk '{printf "  合計       : %s\n", $1}'
        du -sh "$kit/.kit/driver" 2>/dev/null | awk '{printf "  ドライバ   : %s\n", $1}'
        du -sh "$kit/.kit/docker-images" 2>/dev/null | awk '{printf "  イメージ   : %s\n", $1}'
        if [ -s "$kit/.kit/secrets.env.template" ]; then
            say "  工場WiFi   : 載っています"
        else
            say "  工場WiFi   : ❗載っていません（新機は工場網へ繋がれません）"
        fi
        say
        say "USB を安全に取り外して、新しい Pi に挿してください。"
        say "新機では presence-hub-kit の「このUSBからコピー」を開きます。"
    else
        say "❌ 作成に失敗しました（上のメッセージが原因です）"
    fi
    return "$rc"
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
