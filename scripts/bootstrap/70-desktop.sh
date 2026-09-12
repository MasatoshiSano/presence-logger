#!/usr/bin/env bash
# 70-desktop.sh — デスクトップのアイコン一式を配置する。
#
# 「信頼して実行」を自動で付け、実行ビットも立てる。手でファイルを開く必要はない。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
# shellcheck source=scripts/lib/site-env.sh
source "$REPO_DIR/scripts/lib/site-env.sh"
# shellcheck source=scripts/copy-hub-from-usb.sh
source "$REPO_DIR/scripts/copy-hub-from-usb.sh"

main() {
    site_env_require
    local user="${SUDO_USER:-$USER}" home tools desk
    home="$(getent passwd "$user" | cut -d: -f6)"
    desk="$home/Desktop"
    tools="$desk/presence-tools"

    echo "==> ツール本体を配置"
    install -d -o "$user" -g "$user" "$desk" "$tools"
    cp -r "$REPO_DIR/desktop/presence-tools/." "$tools/"
    chmod +x "$tools"/*.sh "$tools"/*.py 2>/dev/null || true

    echo "==> ランチャーを生成"
    local f base
    for f in "$REPO_DIR"/desktop/launchers/*.desktop; do
        [ -f "$f" ] || continue
        base="$(basename "$f")"
        [ "$base" = "ハブ初期設定.desktop" ] && continue
        [ "$base" = "フリート管理.desktop" ] && continue
        sed -e "s|__TOOLS_DIR__|$tools|g" \
            -e "s|__REPO_DIR__|$REPO_DIR|g" \
            -e "s|__HOME_SSID__|${HOME_SSID}|g" \
            "$f" > "$desk/$base"
        copy_trust_desktop "$desk/$base"
    done

    chown -R "$user:$user" "$desk"
    find "$desk" -name '*.desktop' -exec chmod a+x {} +
    echo "✅ デスクトップに配置しました"
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
