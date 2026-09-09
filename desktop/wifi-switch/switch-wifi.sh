#!/usr/bin/env bash
# switch-wifi.sh <接続名> <ifname> [--warn-disconnect <保守用SSID>]
#
# 既に NetworkManager に保存済みの接続へ切り替えるだけ。パスワードは持たない。
# 保存済みでない接続へは切り替えられない(フェーズ70 が secrets.env から作る)。
set -uo pipefail

wifi_switch_parse_conf() {
    local f="${1:?}"
    grep -v '^[[:space:]]*#' "$f" | grep -v '^[[:space:]]*$' \
        | awk '{printf "%s|%s|%s|%s|%s\n", $1, $2, $3, $4, $5}'
}

warn_disconnect_message() {
    local admin_ssid="${1:?}"
    cat <<EOF
⚠ 注意: このネットワークに切り替えると、$admin_ssid から離れます。
   $admin_ssid 経由でしか届かない接続(遠隔操作・Claude Code 等)は切れます。
   実行中の作業があれば、先に終わらせてください。
EOF
}

switch_wifi() {
    local conn="${1:?接続名}" ifname="${2:?ifname}"
    echo "→ $ifname を $conn に切り替えます..."
    if nmcli --wait 20 connection up "$conn" ifname "$ifname" >/dev/null 2>&1; then
        echo "✅ 接続しました: $conn"
    else
        echo "❌ 接続に失敗しました（圏外か、保存情報が無い可能性があります）"
        echo "   保存済み接続の一覧: nmcli -t -f NAME connection show"
        return 1
    fi
    nmcli -t -f DEVICE,STATE,CONNECTION dev status | grep "^${ifname}:" || true
}

main() {
    local conn="${1:?接続名を指定してください}" ifname="${2:?ifname を指定してください}"
    if [ "${3:-}" = "--warn-disconnect" ]; then
        warn_disconnect_message "${4:?保守用SSID}"
        read -r -p "続行しますか？ (y/N): " ans
        case "$ans" in y|Y) ;; *) echo "中止しました。"; return 0 ;; esac
        echo
    fi
    switch_wifi "$conn" "$ifname"
    echo; read -n1 -r -p 'Enterキーで閉じます... ' _
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
