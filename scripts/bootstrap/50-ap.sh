#!/usr/bin/env bash
# 50-ap.sh — ドングルで子Pi 用 AP を立てる。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
# shellcheck source=scripts/lib/site-env.sh
source "$REPO_DIR/scripts/lib/site-env.sh"

ap_ssid_on_air() {
    local ssid="$1"
    nmcli -t -f SSID device wifi list 2>/dev/null | grep -Fxq "$ssid"
}

main() {
    site_env_require
    if [ "${AP_FORCE:-}" != "1" ] && ap_ssid_on_air "$AP_SSID"; then
        echo "周囲に同じ AP 名が見えます: $AP_SSID" >&2
        echo "  親機の AP がまだ上がっている可能性があります。落としてから再実行するか、" >&2
        echo "  AP_FORCE=1 で続行してください。" >&2
        return 1
    fi

    export AP_IF AP_SSID AP_BAND AP_CHANNEL
    export AP_CONN="${AP_CONN:-presence-hub-ap}"
    export UFI_CONN="${HOME_SSID:-}"
    export SECRETS_ENV="${SECRETS_ENV:-/etc/presence-logger/secrets.env}"
    bash "$REPO_DIR/desktop/presence-tools/setup-dongle-ap.sh" || return 1

    if [ "$AP_GW_IP" != "10.42.0.1" ]; then
        echo "==> AP ゲートウェイを $AP_GW_IP に"
        nmcli connection modify "$AP_CONN" ipv4.addresses "$AP_GW_IP/24" || return 1
        nmcli connection up "$AP_CONN" || return 1
    fi
    echo "✅ 子AP を起動しました ($AP_SSID / $AP_GW_IP)"
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
