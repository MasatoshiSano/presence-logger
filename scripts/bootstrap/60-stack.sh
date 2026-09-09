#!/usr/bin/env bash
# 60-stack.sh — コンテナを起動し、再起動でも復活するようにする。
#
# 前提: 子AP が上がっていること。docker-compose.override.yml が mosquitto を
# AP のゲートウェイIPにバインドするため、そのIPが存在しないと起動できない。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
# shellcheck source=scripts/lib/site-env.sh
source "$REPO_DIR/scripts/lib/site-env.sh"

stack_services() {
    printf 'mosquitto\noracle-jdbc\nbridge\n'
    [ "${HUB_MODE:-1}" = "1" ] || printf 'detector\n'
}

stack_write_env() {
    local dst="${1:-$REPO_DIR/.env}"
    printf '# 自動生成: scripts/bootstrap/60-stack.sh\nAP_GW_IP=%s\n' "$AP_GW_IP" > "$dst"
}

main() {
    site_env_require
    stack_write_env "$REPO_DIR/.env"

    if ! ip -4 addr show | grep -q "inet $AP_GW_IP/"; then
        echo "⚠ $AP_GW_IP がホストに付いていません。先にフェーズ50(子AP)を実行してください" >&2
        return 1
    fi

    echo "==> コンテナを起動"
    local svcs; svcs="$(stack_services | tr '\n' ' ')"
    # shellcheck disable=SC2086
    ( cd "$REPO_DIR" && docker compose up -d --build $svcs ) || return 1

    echo "==> systemd で常駐化"
    HUB_MODE="$HUB_MODE" REPO_DIR="$REPO_DIR" \
        bash "$REPO_DIR/desktop/presence-tools/setup-autostart.sh" || return 1

    echo "==> フリート監視を常駐化"
    install -m 644 "$REPO_DIR/fleet_ui/systemd/fleet-ui.service" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --now fleet-ui.service

    docker ps --format '    {{.Names}}  {{.Status}}'
    ss -ltn | grep 8090 || true
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
