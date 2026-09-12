#!/usr/bin/env bash
# 60-stack.sh — コンテナを起動し、再起動でも復活するようにする。
#
# USB キットのイメージ tar があれば docker load し、--no-build で上げる。
# detector はハブでは起動しない。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
# shellcheck source=scripts/lib/site-env.sh
source "$REPO_DIR/scripts/lib/site-env.sh"
# shellcheck source=scripts/lib/docker-run.sh
source "$REPO_DIR/scripts/lib/docker-run.sh"

stack_services() {
    printf 'mosquitto\noracle-jdbc\nbridge\n'
    [ "${HUB_MODE:-1}" = "1" ] || printf 'detector\n'
}

stack_compose_up_args() {
    local kit="${1:-$REPO_DIR/.kit/docker-images}"
    local svcs
    svcs="$(HUB_MODE="${HUB_MODE:-1}" stack_services | tr '\n' ' ')"
    if ls "$kit"/*.tar >/dev/null 2>&1; then
        echo "up -d --no-build $svcs"
    else
        echo "up -d --build $svcs"
    fi
}

stack_load_kit_images() {
    local kit="${1:-$REPO_DIR/.kit/docker-images}" tar
    ls "$kit"/*.tar >/dev/null 2>&1 || return 0
    for tar in "$kit"/*.tar; do
        [ -f "$tar" ] || continue
        echo "==> docker load $tar"
        docker load -i "$tar" || return 1
    done
}

stack_write_env() {
    local dst="${1:-$REPO_DIR/.env}"
    cat > "$dst" <<EOF
# 自動生成: scripts/bootstrap/60-stack.sh
AP_GW_IP=${AP_GW_IP}
COMPOSE_PROJECT_NAME=presence-logger
EOF
}

main() {
    site_env_require
    stack_write_env "$REPO_DIR/.env"

    if ! ip -4 addr show 2>/dev/null | grep -q "inet $AP_GW_IP/"; then
        echo "⚠ $AP_GW_IP がホストに付いていません。先にフェーズ50(子AP)を実行してください" >&2
        return 1
    fi

    local kit="$REPO_DIR/.kit/docker-images"
    stack_load_kit_images "$kit" || return 1

    echo "==> コンテナを起動"
    local args
    args="$(stack_compose_up_args "$kit")"
    # shellcheck disable=SC2086
    ( cd "$REPO_DIR" && docker_compose $args ) || return 1

    echo "==> systemd で常駐化"
    HUB_MODE="$HUB_MODE" REPO_DIR="$REPO_DIR" \
        bash "$REPO_DIR/desktop/presence-tools/setup-autostart.sh" || return 1

    echo "==> 子の付け替えはターミナルの「子をこのハブへ付ける」を使う"
    docker ps --format '    {{.Names}}  {{.Status}}' 2>/dev/null || true
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
