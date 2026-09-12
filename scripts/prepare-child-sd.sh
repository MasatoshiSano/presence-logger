#!/usr/bin/env bash
# prepare-child-sd.sh — クローンした子PiのSDに、このハブの公開鍵と AP 参加情報を書く。
#
# 旧親が止まっている・別工場網だと、フリート管理の「他のハブから引き継ぐ」は
# 工場網経由で旧親へ SSH できない。子のSDを手元で直すのが残る道。
# ホスト名と id_names_config.json（局番号）は触らない。
set -uo pipefail

PREPARE_REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/site-env.sh
source "$PREPARE_REPO_DIR/scripts/lib/site-env.sh"

child_sd_find_root() {
    local base="${1:-/media}"
    local f
    f="$(find "$base" -maxdepth 6 -type f -path '*/home/pi/id_names_config.json' 2>/dev/null | head -1 || true)"
    [ -n "$f" ] || return 1
    dirname "$(dirname "$(dirname "$f")")"
}

child_sd_is_child_root() {
    local root="${1:?}"
    if [ ! -f "$root/home/pi/id_names_config.json" ]; then
        echo "子PiのSDではありません（id_names_config.json が無い）: $root" >&2
        return 1
    fi
    if [ -f "$root/etc/presence-logger/profiles.yaml" ]; then
        echo "これはハブのSDです。子Piのカードを挿してください: $root" >&2
        return 1
    fi
    return 0
}

child_sd_install_pubkey() {
    local root="${1:?}" pub="${2:?}" dest owner_uid
    [ -f "$pub" ] || { echo "公開鍵がありません: $pub" >&2; return 1; }
    mkdir -p "$root/home/pi/.ssh"
    dest="$root/home/pi/.ssh/authorized_keys"
    touch "$dest"
    if grep -qxF "$(tr -d '\r' < "$pub" | head -1)" "$dest" 2>/dev/null; then
        echo "公開鍵は既に入っています"
    else
        cat "$pub" >> "$dest"
        echo "公開鍵を authorized_keys に足しました"
    fi
    chmod 700 "$root/home/pi/.ssh"
    chmod 600 "$dest"
    owner_uid="$(stat -c %u "$root/home/pi" 2>/dev/null || echo 1000)"
    chown "$owner_uid:$owner_uid" "$root/home/pi/.ssh" "$dest" 2>/dev/null || true
}

# NetworkManager の keyfile は未引用だと # 以降がコメントになる。
# 引用符とバックスラッシュだけエスケープして二重引用符で囲む。
child_sd_nm_quote() {
    local v="${1:-}"
    v="${v//\\/\\\\}"
    v="${v//\"/\\\"}"
    printf '"%s"' "$v"
}

child_sd_write_wifi() {
    local root="${1:?}" ssid="${2:?}" psk="${3:?}"
    local dir="$root/etc/NetworkManager/system-connections"
    local dest="$dir/presence-hub-join.nmconnection"
    local uuid qssid qpsk
    mkdir -p "$dir"
    uuid="$(cat /proc/sys/kernel/random/uuid 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"
    qssid="$(child_sd_nm_quote "$ssid")"
    qpsk="$(child_sd_nm_quote "$psk")"
    umask 077
    cat > "$dest" <<EOF
[connection]
id=presence-hub-join
uuid=$uuid
type=wifi
autoconnect=true
autoconnect-priority=200

[wifi]
mode=infrastructure
ssid=$qssid

[wifi-security]
key-mgmt=wpa-psk
psk=$qpsk

[ipv4]
method=auto

[ipv6]
method=ignore
EOF
    chmod 600 "$dest"
    chown 0:0 "$dest" 2>/dev/null || true
}

# 旧ハブの Wi-Fi プロファイルが残ると、同居時にクローンが古い AP へ戻る。
child_sd_disable_other_wifi() {
    local root="${1:?}"
    local dir="$root/etc/NetworkManager/system-connections"
    local f tmp
    [ -d "$dir" ] || return 0
    shopt -s nullglob
    for f in "$dir"/*.nmconnection; do
        [ "$(basename "$f")" = "presence-hub-join.nmconnection" ] && continue
        grep -qE '^type=(wifi|802-11-wireless)$' "$f" || continue
        tmp="$(mktemp)"
        awk '
            BEGIN { in_c=0; seen=0 }
            /^\[connection\]/ { in_c=1; print; next }
            /^\[/ {
                if (in_c && !seen) print "autoconnect=false"
                in_c=0
            }
            in_c && /^autoconnect=/ { print "autoconnect=false"; seen=1; next }
            { print }
            END { if (in_c && !seen) print "autoconnect=false" }
        ' "$f" > "$tmp" && cat "$tmp" > "$f"
        rm -f "$tmp"
        chmod 600 "$f" 2>/dev/null || true
    done
}

child_sd_prepare() {
    local root="${1:?}" pub="${2:?}" ssid="${3:?}" psk="${4:?}"
    child_sd_is_child_root "$root" || return 1
    child_sd_install_pubkey "$root" "$pub" || return 1
    child_sd_write_wifi "$root" "$ssid" "$psk" || return 1
    child_sd_disable_other_wifi "$root" || return 1
}

main() {
    local root="${1:-}" pub ssid psk
    pub="${CHILD_SD_PUBKEY:-$HOME/.ssh/id_ed25519.pub}"
    if [ -z "$root" ]; then
        root="$(child_sd_find_root /media)" || {
            echo "子PiのSDが見つかりません。USBカードリーダで挿してから再実行するか、" >&2
            echo "  bash scripts/prepare-child-sd.sh /media/pi/rootfs" >&2
            echo "のようにマウント先を渡してください。" >&2
            return 1
        }
    fi
    if [[ "$root" != /* || "$root" == *..* ]]; then
        echo "マウント先が不正です: $root" >&2
        return 1
    fi
    child_sd_is_child_root "$root" || return 1

    ssid=""
    psk=""
    if [ -f "$PREPARE_REPO_DIR/.kit/ap-join.env" ]; then
        ssid="$(grep -E '^AP_SSID=' "$PREPARE_REPO_DIR/.kit/ap-join.env" | head -1 | cut -d= -f2-)"
        psk="$(grep -E '^WIFI_AP_PSK=' "$PREPARE_REPO_DIR/.kit/ap-join.env" | head -1 | cut -d= -f2-)"
    fi
    if [ -z "$ssid" ] || [ -z "$psk" ]; then
        echo "このハブの AP 名またはパスワードが読めません。.kit/ap-join.env を確認してください。" >&2
        return 1
    fi
    if [ ! -f "$pub" ]; then
        echo "このハブの公開鍵がありません: $pub" >&2
        echo "  ssh-keygen -t ed25519 を実行してください。" >&2
        return 1
    fi

    echo "子PiのSD: $root"
    echo "  ホスト名と局番号はそのままにします。"
    echo "  このハブの公開鍵と AP（$ssid）を書き込みます。"
    if [ "$(id -u)" -ne 0 ]; then
        # sudo すると HOME が /root になる。公開鍵のパスと HOME を明示して渡す。
        sudo env CHILD_SD_PUBKEY="$pub" HOME="$HOME" \
            bash "$PREPARE_REPO_DIR/scripts/prepare-child-sd.sh" "$root" || return 1
        echo
        echo "SD を外して子Pi に挿し、電源を入れてください。"
        echo "起動したらデスクトップの「子をこのハブへ付ける」を開き、"
        echo "  1) すでに動いている子を移す → 3) 子はもうこのハブの AP に繋がっている"
        echo "を選んでください（トップの番号 3 はありません）。"
        echo "2) 新しい子を増やす は局番号が空になり名前が変わるので、既存の子には使わないでください。"
        read -r -p "Enterで閉じる " _
        return 0
    fi
    child_sd_prepare "$root" "$pub" "$ssid" "$psk" || return 1
    echo "✅ 書き込みました。SD を外して子Pi に挿してください。"
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
