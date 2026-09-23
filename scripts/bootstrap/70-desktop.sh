#!/usr/bin/env bash
# 70-desktop.sh — デスクトップのアイコン一式を配置し、WiFi切替の接続を播種する。
#
# 「信頼して実行」を自動で付け、実行ビットも立てる。手でファイルを開く必要はない。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
ETC_DIR="${ETC_DIR:-/etc/presence-logger}"
# shellcheck source=scripts/lib/site-env.sh
source "$REPO_DIR/scripts/lib/site-env.sh"
# shellcheck source=scripts/copy-hub-from-usb.sh
source "$REPO_DIR/scripts/copy-hub-from-usb.sh"
# shellcheck source=desktop/wifi-switch/switch-wifi.sh
source "$REPO_DIR/desktop/wifi-switch/switch-wifi.sh"

desktop_render_launcher() {
    local tmpl="$1" conn="$2" ifname="$3" label="$4" warn="$5" tools="$6" admin="$7"
    local warn_arg="" comment="$label に切り替えます"
    if [ "$warn" = "warn" ]; then
        warn_arg=" --warn-disconnect \"$admin\""
        comment="$label に切り替えます（$admin から離れます）"
    fi
    sed -e "s|__LABEL__|$label|g" -e "s|__IFNAME__|$ifname|g" \
        -e "s|__CONN__|$conn|g" -e "s|__TOOLS_DIR__|$tools|g" \
        -e "s|__COMMENT__|$comment|g" -e "s|__WARN_ARG__|$warn_arg|g" "$tmpl"
}

desktop_seed_profile() {
    local conn="$1" ifname="$2" psk_key="$3" secrets="${4:-$ETC_DIR/secrets.env}"
    # フェーズ50 が presence-hub-ap の所有者。psk_key=- は播種しない。
    [ "$psk_key" = "-" ] && return 0
    nmcli -t -f NAME connection show 2>/dev/null | grep -qFx "$conn" && return 0
    local psk; psk="$(grep -E "^$psk_key=" "$secrets" 2>/dev/null | head -1 | cut -d= -f2-)"
    if [ -z "$psk" ]; then
        echo "$psk_key が $secrets にありません。$conn は作成しませんでした" >&2
        return 1
    fi
    nmcli connection add type wifi con-name "$conn" ifname "$ifname" ssid "$conn" \
        802-11-wireless-security.key-mgmt wpa-psk \
        802-11-wireless-security.psk "$psk" \
        connection.autoconnect yes >/dev/null
}

main() {
    site_env_require
    local user="${SUDO_USER:-$USER}" home tools desk
    home="$(getent passwd "$user" | cut -d: -f6)"
    desk="$home/Desktop"; tools="$desk/presence-tools"

    echo "==> ツール本体を配置"
    install -d -o "$user" -g "$user" "$desk" "$tools" "$desk/WiFi切替"
    cp -r "$REPO_DIR/desktop/presence-tools/." "$tools/"
    cp "$REPO_DIR/desktop/wifi-switch/switch-wifi.sh" "$desk/WiFi切替/"
    chmod +x "$tools"/*.sh "$desk/WiFi切替/switch-wifi.sh"
    chmod +x "$tools"/*.py 2>/dev/null || true

    echo "==> ランチャーを生成"
    local f base
    for f in "$REPO_DIR"/desktop/launchers/*.desktop; do
        [ -f "$f" ] || continue
        base="$(basename "$f")"
        # ハブ初期設定はキットのコピー時に置かれる(まだ site.env が無い時点で要る)。
        # フリート管理(ブラウザUI)はターミナルの「子をこのハブへ付ける」に替わった。
        [ "$base" = "ハブ初期設定.desktop" ] && continue
        [ "$base" = "フリート管理.desktop" ] && continue
        sed -e "s|__TOOLS_DIR__|$tools|g" \
            -e "s|__REPO_DIR__|$REPO_DIR|g" \
            -e "s|__HOME_SSID__|${HOME_SSID}|g" \
            "$f" > "$desk/$base"
        copy_trust_desktop "$desk/$base"
    done

    local conf="$REPO_DIR/wifi-switch.conf"
    if [ -f "$conf" ]; then
        echo "==> WiFi切替のランチャーと接続を用意"
        local conn ifname label warn key
        while IFS='|' read -r conn ifname label warn key; do
            desktop_render_launcher "$REPO_DIR/desktop/wifi-switch/launcher.desktop.tmpl" \
                "$conn" "$ifname" "$label" "$warn" "$desk/WiFi切替" "$ADMIN_SSID" \
                > "$desk/WiFi切替/$label.desktop"
            copy_trust_desktop "$desk/WiFi切替/$label.desktop"
            [ "$key" = "-" ] || desktop_seed_profile "$conn" "$ifname" "$key" || true
        done < <(wifi_switch_parse_conf "$conf")
    else
        echo "wifi-switch.conf がありません。WiFi切替はスキップします"
    fi

    chown -R "$user:$user" "$desk"
    find "$desk" -name '*.desktop' -exec chmod a+x {} +
    echo "✅ デスクトップに配置しました"
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
