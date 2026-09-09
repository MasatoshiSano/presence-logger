#!/usr/bin/env bash
# copy-hub-from-usb.sh — USB キットをこの Pi のホームへコピーし、初期設定アイコンを置く。
#
# USB 上では copy-to-this-pi.sh という名前でも同じファイル。
# FAT は実行ビットを落とすので、必ず `bash このファイル` で呼ぶ。
set -uo pipefail

COPY_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# USB 上ではキット直下、リポジトリでは scripts/ 配下。
if [ -f "$COPY_HERE/lib/kit-copy.sh" ]; then
    # shellcheck source=scripts/lib/kit-copy.sh
    source "$COPY_HERE/lib/kit-copy.sh"
elif [ -f "$COPY_HERE/scripts/lib/kit-copy.sh" ]; then
    # shellcheck source=scripts/lib/kit-copy.sh
    source "$COPY_HERE/scripts/lib/kit-copy.sh"
elif [ -f "$COPY_HERE/payload/presence-logger/scripts/lib/kit-copy.sh" ]; then
    # shellcheck source=scripts/lib/kit-copy.sh
    source "$COPY_HERE/payload/presence-logger/scripts/lib/kit-copy.sh"
fi

copy_find_kit() {
    local root="${1:-/media}"
    local origin
    origin="$(find "$root" -maxdepth 5 -type f -path '*/presence-hub-kit/.kit/origin.env' 2>/dev/null | head -1 || true)"
    [ -n "$origin" ] || return 1
    dirname "$(dirname "$origin")"
}

copy_fix_permissions() {
    local dest="$1" user="${2:-}"
    find "$dest" -type f \( -name '*.sh' -o -name '*.desktop' -o -name '*.py' \) \
        -exec chmod a+x {} + 2>/dev/null || true
    if [ -n "$user" ] && [ "$(id -u)" -eq 0 ]; then
        chown -R "$user:$user" "$dest"
    fi
}

copy_trust_desktop() {
    local f="$1"
    chmod a+x "$f" 2>/dev/null || true
    if command -v gio >/dev/null 2>&1; then
        gio set "$f" metadata::trusted true 2>/dev/null || true
    fi
}

copy_install_setup_icon() {
    local dest="$1" desk="$2"
    local tmpl="$dest/desktop/launchers/ハブ初期設定.desktop"
    mkdir -p "$desk"
    if [ ! -f "$tmpl" ]; then
        echo "ハブ初期設定.desktop がキットにありません" >&2
        return 1
    fi
    sed -e "s|__REPO_DIR__|$dest|g" -e "s|__TOOLS_DIR__|$desk/presence-tools|g" \
        "$tmpl" > "$desk/ハブ初期設定.desktop"
    copy_trust_desktop "$desk/ハブ初期設定.desktop"
}

copy_hub_from_usb() {
    local kit="$1" dest="$2" desk="$3"
    if [ ! -f "$kit/.kit/origin.env" ]; then
        echo "キットではありません（.kit/origin.env が無い）: $kit" >&2
        return 1
    fi
    mkdir -p "$dest" "$desk"
    if [ -d "$kit/payload/presence-logger" ]; then
        kit_copy_dir "$kit/payload/presence-logger" "$dest" || return 1
    else
        echo "payload/presence-logger がありません: $kit" >&2
        return 1
    fi
    mkdir -p "$dest/.kit"
    kit_copy_dir "$kit/.kit" "$dest/.kit" || return 1
    copy_fix_permissions "$dest"
    copy_install_setup_icon "$dest" "$desk"
    echo "✅ $dest へコピーしました"
    echo "   デスクトップの「ハブ初期設定」をクリックしてください"
    echo "   （この時点では AP もコンテナも起動していません）"
}

copy_self_kit() {
    local here
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [ -f "$here/.kit/origin.env" ]; then
        printf '%s\n' "$here"
        return 0
    fi
    return 1
}

main() {
    local kit="${1:-}" dest desk user home
    user="${SUDO_USER:-$USER}"
    home="$(getent passwd "$user" | cut -d: -f6)"
    home="${home:-$HOME}"
    dest="${COPY_DEST:-$home/projects/presence-logger}"
    desk="${COPY_DESKTOP:-$home/Desktop}"

    if [ -z "$kit" ]; then
        kit="$(copy_self_kit || true)"
    fi
    if [ -z "$kit" ]; then
        kit="$(copy_find_kit /media || true)"
    fi
    if [ -z "$kit" ]; then
        kit="$(copy_find_kit /run/media || true)"
    fi
    if [ -z "$kit" ]; then
        echo "USB キットが見つかりません。presence-hub-kit を挿してから再実行してください" >&2
        echo "  手動: bash copy-to-this-pi.sh /media/pi/USB名/presence-hub-kit" >&2
        return 1
    fi
    copy_hub_from_usb "$kit" "$dest" "$desk"
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
