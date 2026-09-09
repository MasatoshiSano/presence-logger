#!/usr/bin/env bash
# bootstrap-hub.sh — 新しいハブ Pi を立ち上げる。
#
#   sudo bash scripts/bootstrap-hub.sh          全フェーズ
#   sudo bash scripts/bootstrap-hub.sh 10       フェーズ10 だけ
#   sudo bash scripts/bootstrap-hub.sh 30 60    フェーズ30〜60
#   bash scripts/bootstrap-hub.sh --list        フェーズ一覧
#
# 実処理は持たない。scripts/bootstrap/NN-*.sh を番号順に呼ぶだけ。
set -uo pipefail

BOOTSTRAP_REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOTSTRAP_DIR="${BOOTSTRAP_DIR:-$BOOTSTRAP_REPO_DIR/scripts/bootstrap}"

bootstrap_discover_phases() {
    local dir="${1:-$BOOTSTRAP_DIR}"
    find "$dir" -maxdepth 1 -name '[0-9][0-9]-*.sh' -type f | sort
}

bootstrap_select_phases() {
    local dir="${1:-$BOOTSTRAP_DIR}" from="${2:-0}" to="${3:-99}" p num
    while IFS= read -r p; do
        num="$(basename "$p")"; num="${num%%-*}"
        if [ "$((10#$num))" -ge "$((10#$from))" ] && [ "$((10#$num))" -le "$((10#$to))" ]; then
            printf '%s\n' "$p"
        fi
    done < <(bootstrap_discover_phases "$dir")
}

main() {
    if [ "${1:-}" = "--list" ]; then
        bootstrap_discover_phases | while IFS= read -r p; do
            printf '  %s\n' "$(basename "$p")"
        done
        return 0
    fi
    if [ "$EUID" -ne 0 ]; then
        echo "root で実行してください: sudo bash $0 $*" >&2
        return 1
    fi
    local phases; phases="$(bootstrap_select_phases "$BOOTSTRAP_DIR" "${1:-0}" "${2:-${1:-99}}")"
    if [ -z "$phases" ]; then
        echo "該当するフェーズがありません。--list で確認してください" >&2
        return 1
    fi
    local p
    while IFS= read -r p; do
        printf '\n==================== %s ====================\n' "$(basename "$p")"
        bash "$p" || { echo "失敗しました: $(basename "$p")" >&2; return 1; }
    done <<< "$phases"
    printf '\n完了しました。\n'
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
