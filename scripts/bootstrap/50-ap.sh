#!/usr/bin/env bash
# 50-ap.sh — 子Pi 用の AP を立てる。実処理は既存の setup-dongle-ap.sh に任せ、
# ここは site.env の値を環境変数として渡すことと、同一SSID の重複検出を担う。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
# shellcheck source=scripts/lib/site-env.sh
source "$REPO_DIR/scripts/lib/site-env.sh"

ap_duplicate_ssid_present() {
    local ssid="${1:-$AP_SSID}"
    nmcli -t -f SSID,SIGNAL,SECURITY dev wifi list 2>/dev/null \
        | cut -d: -f1 | grep -qFx "$ssid"
}

# setup-dongle-ap.sh は ipv4.method shared を使うため、GW IP は NetworkManager が
# 既定の 10.42.0.1/24 を自動で付ける。それ以外にするには ipv4.addresses の
# 明示指定が要る(=増設時。子側の send_target_config.json の変更も必要)。
ap_needs_explicit_address() {
    [ "${AP_GW_IP:-10.42.0.1}" != "10.42.0.1" ]
}

ap_env_args() {
    printf 'AP_IF=%s\n'      "$AP_IF"
    printf 'AP_SSID=%s\n'    "$AP_SSID"
    printf 'AP_BAND=%s\n'    "$AP_BAND"
    printf 'AP_CHANNEL=%s\n' "$AP_CHANNEL"
    printf 'UFI_CONN=%s\n'   "$HOME_SSID"
    printf 'AP_CONN=%s-ap\n' "$AP_SSID"
}

main() {
    site_env_require
    if [ "${1:-}" != "--force" ] && ap_duplicate_ssid_present "$AP_SSID"; then
        cat >&2 <<EOF
⚠ 同じ SSID の AP が既に見えています: $AP_SSID

  このまま起動すると同名の AP が2つになり、子Piがどちらに繋ぐか不定になります
  (DEPLOY.md に記録のある相互切断事故と同じ構図)。

  引っ越しなら、先に旧ハブの AP を落としてください:
      旧ハブで: sudo nmcli connection down ${AP_SSID}-ap
                sudo nmcli connection modify ${AP_SSID}-ap connection.autoconnect no

  増設なら、site.env の AP_SSID と AP_GW_IP を別の値にしてください。

  それでも続けるなら: bash $0 --force
EOF
        return 1
    fi

    local kv
    while IFS= read -r kv; do export "${kv?}"; done < <(ap_env_args)
    bash "$REPO_DIR/desktop/presence-tools/setup-dongle-ap.sh" || return 1

    if ap_needs_explicit_address; then
        echo "==> AP のゲートウェイIPを $AP_GW_IP に固定"
        nmcli connection modify "${AP_SSID}-ap" ipv4.addresses "${AP_GW_IP}/24"
        nmcli connection up "${AP_SSID}-ap"
        cat <<EOF

⚠ AP のIPが既定(10.42.0.1)ではありません。各子Pi の
  ~/send_target_config.json の "host" を $AP_GW_IP へ変更する必要があります。
EOF
    fi
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
