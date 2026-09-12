#!/usr/bin/env bash
# pack-hub-usb.sh — 親機から USB へ、2台目ハブ用のキットを書き出す。
#
#   bash scripts/pack-hub-usb.sh /media/pi/USB
#
# キットには親の子一覧・Oracle パスワード・AP パスワードを載せない。
set -uo pipefail

PACK_REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/kit-copy.sh
source "$PACK_REPO_DIR/scripts/lib/kit-copy.sh"

pack_rsync_excludes() {
    cat <<'EOF'
.venv
venv
site.env
wifi-switch.conf
fleet/children.conf
fleet/known_macs.json
.kit
*.db
*.db-wal
*.db-shm
*.db-journal
__pycache__
.pytest_cache
.ruff_cache
EOF
}

pack_hub_images() {
    cat <<'EOF'
eclipse-mosquitto:2
presence-logger-oracle-jdbc:latest
presence-logger-bridge:latest
EOF
}

pack_strip_secrets() {
    local re='^(ORACLE_PASSWORD_|WALLET_PASSWORD_|WIFI_AP_PSK=)'
    if [ -n "${1:-}" ] && [ -f "$1" ]; then
        grep -vE "$re" "$1" || true
    else
        grep -vE "$re" || true
    fi
}

pack_copy_tree() {
    local src="$1" dst="$2"
    pack_rsync_excludes | kit_copy_tree_filtered "$src" "$dst"
}

pack_write_empty_children() {
    local repo="$1"
    mkdir -p "$repo/fleet"
    cat > "$repo/fleet/children.conf" <<'EOF'
# このハブの子Pi。親機の一覧はコピーしていない。
# デスクトップの「子をこのハブへ付ける」から追加する。
EOF
}

_pack_get() {
    local key="$1" file="$2" line
    line="$(grep -E "^${key}=" "$file" 2>/dev/null | head -1 || true)"
    line="${line#*=}"
    line="${line%%#*}"
    line="${line%"${line##*[![:space:]]}"}"
    line="${line#"${line%%[![:space:]]*}"}"
    if [ "${#line}" -ge 2 ]; then
        case "$line" in
            \"*\") line="${line:1:-1}" ;;
            \'*\') line="${line:1:-1}" ;;
        esac
    fi
    printf '%s\n' "$line"
}

_pack_origin_kv() {
    local k="$1" v="$2"
    if [[ "$v" == *" "* ]]; then
        printf '%s="%s"\n' "$k" "$v"
    else
        printf '%s=%s\n' "$k" "$v"
    fi
}

pack_write_origin() {
    local site="$1" dest="$2"
    mkdir -p "$(dirname "$dest")"
    {
        _pack_origin_kv ORIGIN_HOSTNAME "$(_pack_get HUB_HOSTNAME "$site")"
        _pack_origin_kv ORIGIN_FACTORY_IP "$(_pack_get FACTORY_IP "$site")"
        _pack_origin_kv ORIGIN_FACTORY_SSID "$(_pack_get FACTORY_SSID "$site")"
        _pack_origin_kv ORIGIN_FACTORY_GW "$(_pack_get FACTORY_GW "$site")"
        _pack_origin_kv ORIGIN_FACTORY_DNS "$(_pack_get FACTORY_DNS "$site")"
        _pack_origin_kv ORIGIN_FACTORY_SUBNETS "$(_pack_get FACTORY_SUBNETS "$site")"
        _pack_origin_kv ORIGIN_SNTP_SERVERS "$(_pack_get SNTP_SERVERS "$site")"
        _pack_origin_kv ORIGIN_AP_SSID "$(_pack_get AP_SSID "$site")"
        _pack_origin_kv ORIGIN_ORACLE_HOST "$(_pack_get ORACLE_HOST "$site")"
        _pack_origin_kv ORIGIN_ORACLE_PORT "$(_pack_get ORACLE_PORT "$site")"
        _pack_origin_kv ORIGIN_ORACLE_SERVICE "$(_pack_get ORACLE_SERVICE "$site")"
        _pack_origin_kv ORIGIN_ORACLE_USER "$(_pack_get ORACLE_USER "$site")"
        _pack_origin_kv ORIGIN_ORACLE_TABLE "$(_pack_get ORACLE_TABLE "$site")"
        _pack_origin_kv ORIGIN_PARENT_STA_NO1 "$(_pack_get PARENT_STA_NO1 "$site")"
        _pack_origin_kv ORIGIN_PARENT_STA_NO2 "$(_pack_get PARENT_STA_NO2 "$site")"
        _pack_origin_kv ORIGIN_PARENT_STA_NO3 "$(_pack_get PARENT_STA_NO3 "$site")"
    } > "$dest"
}

pack_blank_identity() {
    local src="$1" dest="$2"
    sed -e 's/^HUB_HOSTNAME=.*/HUB_HOSTNAME=/' \
        -e 's/^AP_SSID=.*/AP_SSID=/' \
        -e 's/^FACTORY_IP=.*/FACTORY_IP=/' \
        "$src" > "$dest"
}

pack_copy_driver() {
    local src="${1:-}" dest="$2"
    [ -n "$src" ] && [ -d "$src" ] || return 0
    kit_copy_dir "$src" "$dest"
}

pack_save_images() {
    local dest="$1" img resolved name
    mkdir -p "$dest"
    while IFS= read -r img; do
        [ -n "$img" ] || continue
        if docker image inspect "$img" >/dev/null 2>&1; then
            resolved="$img"
        elif docker image inspect "${img%:latest}" >/dev/null 2>&1; then
            resolved="${img%:latest}"
        else
            echo "イメージが見つかりません: $img （親で docker compose up --build 済みか確認）" >&2
            return 1
        fi
        name="${resolved##*/}"
        name="${name%%:*}"
        echo "==> docker save $resolved"
        docker save -o "$dest/${name}.tar" "$resolved" || return 1
    done < <(pack_hub_images)
}

pack_write_readme() {
    cat > "$1" <<'EOF'
presence-hub-kit — 2台目ハブの USB キット
========================================

この USB には親機の設定一式とコンテナイメージが入っています。
工場WiFi のパスワードも入っています。鍵と同じ扱いをし、使い終わったら消してください。
Oracle のパスワードと新しい AP のパスワードは載せていません（初期設定で聞きます）。

新機（Raspberry Pi OS Desktop が入った素の Pi）での手順:

1. この USB を挿す
2. ファイルマネージャで presence-hub-kit を開き、
   「このUSBからコピー」をダブルクリックする
   （開かないときはターミナルで）
     bash /media/*/presence-hub-kit/copy-to-this-pi.sh
3. デスクトップに「ハブ初期設定」が現れるのでクリックする
4. 工場の SSID・ゲートウェイ・Oracle なども順に答える（Enter でコピー元の値）
5. 終わったら再起動し、デスクトップの「子をこのハブへ付ける」で子を追加する

親機はそのまま運転したままで大丈夫です。新しいハブは別のホスト名・IP・AP名になります。
EOF
}

pack_write_copy_desktop() {
    cat > "$1" <<'EOF'
[Desktop Entry]
Type=Application
Version=1.0
Name=このUSBからコピー
Name[ja]=このUSBからコピー
Comment=この Raspberry Pi にハブ一式をコピーし、初期設定アイコンを置きます
Exec=lxterminal -t "ハブをコピー" -e bash -c "kit=$(find /media /run/media \"$HOME\" -name copy-to-this-pi.sh 2>/dev/null | head -1); if [ -z \"$kit\" ]; then echo USB の copy-to-this-pi.sh が見つかりません; else bash \"$kit\"; fi; echo; read -r -p 'Enterで閉じる '"
Icon=media-removable
Terminal=false
Categories=Utility;
EOF
}

pack_hub_kit() {
    local src="${1:-$PACK_REPO_DIR}" dest_root="${2:?USB のマウント先を指定してください}"
    local kit="$dest_root/presence-hub-kit"
    local site="${PACK_SITE_ENV:-$src/site.env}"
    local secrets="${PACK_SECRETS:-/etc/presence-logger/secrets.env}"
    local driver="${PACK_DRIVER_SRC-}"
    if [ -z "$driver" ]; then
        if [ -d /usr/local/src/8821au ]; then
            driver=/usr/local/src/8821au
        elif [ -d "${HOME:-}/8821au" ]; then
            driver="${HOME}/8821au"
        fi
    fi

    if [ ! -f "$site" ]; then
        echo "親の site.env がありません: $site" >&2
        echo "  親機で site.env を用意してから pack してください" >&2
        return 1
    fi

    mkdir -p "$kit/payload/presence-logger" "$kit/.kit"
    pack_copy_tree "$src" "$kit/payload/presence-logger"
    pack_write_empty_children "$kit/payload/presence-logger"
    pack_write_origin "$site" "$kit/.kit/origin.env"
    pack_blank_identity "$site" "$kit/.kit/site.env.template"
    if [ -f "$secrets" ]; then
        pack_strip_secrets "$secrets" > "$kit/.kit/secrets.env.template"
    else
        printf '# 親の secrets.env がありませんでした。工場WiFi の PSK は後で入れてください。\n' \
            > "$kit/.kit/secrets.env.template"
    fi
    chmod 600 "$kit/.kit/secrets.env.template" "$kit/.kit/origin.env" "$kit/.kit/site.env.template"
    pack_copy_driver "$driver" "$kit/.kit/driver/8821au"
    pack_write_readme "$kit/README.txt"
    pack_write_copy_desktop "$kit/このUSBからコピー.desktop"
    cp "$PACK_REPO_DIR/scripts/copy-hub-from-usb.sh" "$kit/copy-to-this-pi.sh"
    chmod 644 "$kit/このUSBからコピー.desktop" "$kit/copy-to-this-pi.sh" "$kit/README.txt"

    if [ "${PACK_SKIP_DOCKER:-}" = "1" ]; then
        echo "PACK_SKIP_DOCKER=1 のためイメージは載せていません"
    else
        pack_save_images "$kit/.kit/docker-images" || return 1
    fi

    echo "✅ USB キットを書きました: $kit"
    echo "   新機で copy-to-this-pi.sh を実行してください"
}

main() {
    local dest="${1:-}"
    if [ -z "$dest" ]; then
        echo "使い方: bash $0 /media/pi/USBのマウント先" >&2
        return 2
    fi
    pack_hub_kit "$PACK_REPO_DIR" "$dest"
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
