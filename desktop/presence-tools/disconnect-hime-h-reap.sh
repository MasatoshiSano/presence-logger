#!/usr/bin/env bash
# disconnect-hime-h-reap.sh
# HIME-H-REAP を切断し、元のWiFi(UFI_103134)に戻す。
set -uo pipefail

HOLD=0
CONN_NAME="${CONN_NAME:-HIME-H-REAP}"
HUB_MODE="${HUB_MODE:-0}"

say(){ printf '%s\n' "$*"; }
finish(){ [[ "$HOLD" == 1 ]] && { echo; read -rp 'Enterキーで閉じる... ' _; }; exit "${1:-0}"; }

detector_stop() {
    [ "$HUB_MODE" = "1" ] && { say "■ ハブ構成のため検知の停止は不要です"; return 0; }
    docker stop presence-detector >/dev/null 2>&1 && say "■ 検知を停止しました（detector）"
}

main() {
    if [[ $EUID -ne 0 ]]; then
        exec sudo bash "$0" "$@"
    fi

    [[ "${1:-}" == "--hold" ]] && HOLD=1

    # site.env があれば読む(HUB_MODE / HOME_SSID / PROFILE_NAME 等)。
    # 無い機体でも従来どおり動くよう、失敗は無視する。
    _SITE_ENV="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/site.env"
    # shellcheck disable=SC1090
    [ -f "$_SITE_ENV" ] && { set -a; source "$_SITE_ENV"; set +a; }
    HUB_MODE="${HUB_MODE:-0}"
    HOME_PROFILE="${HOME_PROFILE:-${HOME_SSID:-UFI_103134}}"

    detector_stop

    # 子ラズパイ用AP(presence-hub, wlan1)は常時稼働が前提。ここでは止めない
    # （以前は down していたが、NetworkManagerの自動接続で wlan1 が UFI_103134
    #   に取られてしまい、子Piへの経路が失われるバグがあったため廃止）。

    # 工場網(HIME-H-REAP)を停止
    nmcli connection down "$CONN_NAME" >/dev/null 2>&1 || true
    say "■ 工場網 $CONN_NAME を切断しました"

    if nmcli connection up "$HOME_PROFILE" >/dev/null 2>&1; then
        say "✅ $HOME_PROFILE に戻しました"
        finish 0
    fi

    # 指定の戻り先が圏外なら、自動接続に任せる
    say "⚠ $HOME_PROFILE に戻せませんでした（圏外かも）。"
    say "  利用可能なWiFiに自動接続を試みます..."
    nmcli device connect "${IFNAME:-wlan0}" >/dev/null 2>&1 || true
    ACTUAL="$(iwgetid -r "${IFNAME:-wlan0}" 2>/dev/null)"
    say "  現在の接続: ${ACTUAL:-(なし)}"
    finish 0
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
