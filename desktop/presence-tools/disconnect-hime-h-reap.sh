#!/usr/bin/env bash
# disconnect-hime-h-reap.sh
# HIME-H-REAP を切断し、元のWiFi(UFI_103134)に戻す。
set -uo pipefail

if [[ $EUID -ne 0 ]]; then
    exec sudo bash "$0" "$@"
fi

HOLD=0
[[ "${1:-}" == "--hold" ]] && HOLD=1

HOME_PROFILE="${HOME_PROFILE:-UFI_103134}"   # 戻り先（必要なら環境変数で上書き）
CONN_NAME="${CONN_NAME:-HIME-H-REAP}"

say(){ printf '%s\n' "$*"; }
finish(){ [[ "$HOLD" == 1 ]] && { echo; read -rp 'Enterキーで閉じる... ' _; }; exit "${1:-0}"; }

# 検知を停止（detector コンテナ停止 = カメラ取得も停止）
if docker stop presence-detector >/dev/null 2>&1; then
    say "■ 検知を停止しました（detector）"
fi

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
