#!/usr/bin/env bash
# setup-hub-wizard.sh — デスクトップの「ハブ初期設定」から呼ばれる対話セットアップ。
#
# ホスト名・工場網・AP・Oracle を順に聞く。空Enter はコピー元（USBキット）の値。
# site.env と secrets を書いて bootstrap-hub.sh を回す。
set -uo pipefail

WIZARD_REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/site-env.sh
source "$WIZARD_REPO_DIR/scripts/lib/site-env.sh"

wizard_already_configured() {
    [ -f "$1" ]
}

wizard_validate_ap_psk() {
    local psk="$1"
    if [ "${#psk}" -lt 8 ]; then
        echo "AP パスワードは 8 文字以上にしてください" >&2
        return 1
    fi
}

wizard_validate_hostname() {
    local name="$1" origin="$2" allow_same="${3:-0}"
    if ! site_env_valid_hostname "$name"; then
        echo "ホスト名に使えない文字が含まれています: $name" >&2
        echo "  英小文字・数字・ハイフンのみ（先頭末尾はハイフン不可）" >&2
        return 1
    fi
    site_env_load_origin "$origin" || return 1
    if [ "$name" = "${ORIGIN_HOSTNAME:-}" ] && [ "$allow_same" != "1" ]; then
        echo "親機と同じホスト名です: $name" >&2
        echo "  共存するには別の名前にしてください" >&2
        return 1
    fi
}

wizard_validate_factory_ip() {
    local ip="$1" origin="$2" allow_same="${3:-0}" addr
    addr="${ip%/*}"
    if [[ ! "$addr" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
        echo "固定IPの形式が不正です: $ip" >&2
        return 1
    fi
    site_env_load_origin "$origin" || return 1
    if [ "$addr" = "${ORIGIN_FACTORY_IP%/*}" ] && [ "$allow_same" != "1" ]; then
        echo "親機と同じ固定IPです: $addr" >&2
        echo "  工場網で衝突します。情シスへ申請した別のIPを入れてください" >&2
        return 1
    fi
}

wizard_validate_ap_ssid() {
    local ssid="$1" origin="$2" allow_same="${3:-0}"
    if [ -z "$ssid" ]; then
        echo "AP 名が空です" >&2
        return 1
    fi
    site_env_load_origin "$origin" || return 1
    if [ "$ssid" = "${ORIGIN_AP_SSID:-}" ] && [ "$allow_same" != "1" ]; then
        echo "親機と同じ AP 名です: $ssid" >&2
        echo "  子Pi がどちらのハブに付くか不定になります" >&2
        return 1
    fi
}

wizard_validate_ipv4() {
    local ip="$1" label="$2"
    if [[ ! "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
        echo "${label}の形式が不正です: $ip" >&2
        return 1
    fi
}

wizard_validate_port() {
    local port="$1"
    if [[ ! "$port" =~ ^[1-9][0-9]{0,4}$ ]] || [ "$port" -gt 65535 ]; then
        echo "ポート番号が不正です: $port" >&2
        return 1
    fi
}

wizard_sibling_name() {
    local base="${1:-}"
    if [ -z "$base" ]; then
        printf '%s\n' "$2"
        return 0
    fi
    printf '%s-2\n' "$base"
}

wizard_validate_not_empty() {
    local v="$1" label="$2"
    if [ -z "$v" ]; then
        echo "${label}が空です" >&2
        return 1
    fi
}

wizard_shift_sta() {
    local n="${1:-0}"
    if [[ "$n" =~ ^[0-9]+$ ]]; then
        echo $((n + 3))
    else
        echo "$n"
    fi
}

wizard_normalize_factory_ip() {
    local user="$1" hint="${2:-}"
    if [[ "$user" == */* ]]; then
        printf '%s\n' "$user"
        return 0
    fi
    local prefix="${hint#*/}"
    if [[ "$hint" == */* ]] && [ -n "$prefix" ] && [ "$prefix" != "$hint" ]; then
        printf '%s/%s\n' "$user" "$prefix"
    else
        printf '%s/24\n' "$user"
    fi
}

wizard_dump_kv() {
    local k="$1" v="${!k-}"
    if [[ "$v" == *" "* ]]; then
        printf '%s="%s"\n' "$k" "$v"
    else
        printf '%s=%s\n' "$k" "$v"
    fi
}

wizard_render_site_env() {
    local tmpl="$1" origin="$2" hostname="$3" factory_ip="$4" ap_ssid="$5"
    local allow_same="${6:-0}"
    # shellcheck disable=SC1090
    set -a; source "$tmpl"; set +a
    site_env_load_origin "$origin" || return 1
    wizard_validate_hostname "$hostname" "$origin" "$allow_same" || return 1
    wizard_validate_factory_ip "$factory_ip" "$origin" "$allow_same" || return 1
    wizard_validate_ap_ssid "$ap_ssid" "$origin" "$allow_same" || return 1

    HUB_HOSTNAME="$hostname"
    HUB_MODE=1
    local hint="${FACTORY_IP:-$ORIGIN_FACTORY_IP}"
    FACTORY_IP="$(wizard_normalize_factory_ip "$factory_ip" "$hint")"
    AP_SSID="$ap_ssid"
    AP_GW_IP="${AP_GW_IP:-10.42.0.1}"
    [ -n "${WIZ_FACTORY_SSID:-}" ] && FACTORY_SSID="$WIZ_FACTORY_SSID"
    [ -n "${WIZ_FACTORY_GW:-}" ] && FACTORY_GW="$WIZ_FACTORY_GW"
    [ -n "${WIZ_FACTORY_DNS:-}" ] && FACTORY_DNS="$WIZ_FACTORY_DNS"
    [ -n "${WIZ_ORACLE_HOST:-}" ] && ORACLE_HOST="$WIZ_ORACLE_HOST"
    [ -n "${WIZ_ORACLE_PORT:-}" ] && ORACLE_PORT="$WIZ_ORACLE_PORT"
    [ -n "${WIZ_ORACLE_SERVICE:-}" ] && ORACLE_SERVICE="$WIZ_ORACLE_SERVICE"
    [ -n "${WIZ_ORACLE_USER:-}" ] && ORACLE_USER="$WIZ_ORACLE_USER"
    [ -n "${WIZ_ORACLE_TABLE:-}" ] && ORACLE_TABLE="$WIZ_ORACLE_TABLE"
    PARENT_STA_NO1="$(wizard_shift_sta "${ORIGIN_PARENT_STA_NO1:-${PARENT_STA_NO1:-997}}")"
    PARENT_STA_NO2="$(wizard_shift_sta "${ORIGIN_PARENT_STA_NO2:-${PARENT_STA_NO2:-996}}")"
    PARENT_STA_NO3="$(wizard_shift_sta "${ORIGIN_PARENT_STA_NO3:-${PARENT_STA_NO3:-995}}")"

    local k
    for k in HUB_HOSTNAME HUB_MODE \
        FACTORY_SSID FACTORY_IP FACTORY_GW FACTORY_DNS FACTORY_HIDDEN FACTORY_SUBNETS SNTP_SERVERS \
        ORACLE_CLIENT_MODE ORACLE_AUTH_MODE ORACLE_HOST ORACLE_PORT ORACLE_SERVICE \
        ORACLE_USER ORACLE_TABLE ORACLE_PASSWORD_VAR UPCMPFLG UNKNOWN_SSID_POLICY \
        PARENT_STA_NO1 PARENT_STA_NO2 PARENT_STA_NO3 \
        AP_IF AP_SSID AP_GW_IP AP_BAND AP_CHANNEL \
        HOME_SSID ADMIN_SSID; do
        [ -n "${!k+x}" ] || continue
        wizard_dump_kv "$k"
    done
    if [ "$allow_same" = "1" ]; then
        printf 'ORIGIN_ALLOW_SAME_AP=1\n'
        printf 'ORIGIN_REPLACE=1\n'
    fi
}

wizard_merge_secrets() {
    local tmpl="$1" dest="$2" oracle_key="$3" oracle_pass="$4" ap_psk="$5"
    mkdir -p "$(dirname "$dest")"
    {
        if [ -f "$tmpl" ]; then
            grep -vE "^(${oracle_key}|WIFI_AP_PSK)=" "$tmpl" || true
        fi
        printf '%s=%s\n' "$oracle_key" "$oracle_pass"
        printf 'WIFI_AP_PSK=%s\n' "$ap_psk"
    } > "$dest"
    chmod 600 "$dest"
}

wizard_ask() {
    local prompt="$1" default="${2:-}" reply
    if [ -n "$default" ]; then
        read -r -p "$prompt [$default]: " reply
        printf '%s\n' "${reply:-$default}"
    else
        read -r -p "$prompt: " reply
        printf '%s\n' "$reply"
    fi
}

wizard_ask_secret() {
    local prompt="$1" reply
    read -r -s -p "$prompt: " reply
    echo >&2
    printf '%s\n' "$reply"
}

main() {
    local repo="${WIZARD_REPO_DIR}"
    local origin="$repo/.kit/origin.env"
    local tmpl="$repo/.kit/site.env.template"
    local secrets_tmpl="$repo/.kit/secrets.env.template"
    local marker="$repo/.kit/setup-complete"
    local user="${SUDO_USER:-$USER}"

    if wizard_already_configured "$marker"; then
        echo "既に設定済みです（$marker）。"
        echo "やり直すときはそのファイルを消してから、もう一度このアイコンを開いてください。"
        read -r -p "Enterで閉じる " _
        return 0
    fi

    if [ ! -f "$origin" ]; then
        echo "USB キットの origin.env がありません: $origin" >&2
        echo "  親機で bash scripts/pack-hub-usb.sh し直してください" >&2
        read -r -p "Enterで閉じる " _
        return 1
    fi
    if [ ! -f "$tmpl" ]; then
        tmpl="$repo/site.env.example"
    fi

    site_env_load_origin "$origin" || return 1
    # shellcheck disable=SC1090
    set -a; source "$tmpl"; set +a
    site_env_load_origin "$origin" || return 1

    echo "この Raspberry Pi を子Pi専用ハブにします。"
    echo "質問は 1 項目ずつです。[] はコピー元（または推奨）の値。Enter ならそのまま。"
    echo
    echo "  1) 親機と同時に動かす（同じ工場網。このハブの名前・工場IP・子Pi用AP名は親機と別）"
    echo "  2) 親機はもう使わない、または別の工場網"
    echo "     （コピー元と同じ値でもよい。クローンした子が自動で付きます）"
    echo
    local hostname factory_ip factory_ssid factory_gw factory_dns
    local oracle_host oracle_port oracle_service oracle_user oracle_table
    local ap_ssid ap_psk oracle_pass mode allow_same=0
    mode="$(wizard_ask "番号で選ぶ" "1")"
    if [ "$mode" = "2" ]; then
        allow_same=1
    fi
    echo

    echo "----- ① このハブのホスト名 -----"
    echo "Linux がこの Raspberry Pi を呼ぶ名前です（親機は ${ORIGIN_HOSTNAME:-?}）。"
    echo "子Piが探す Wi-Fi 名（親機では ${ORIGIN_AP_SSID:-?}）ではありません。"
    if [ "$allow_same" != "1" ]; then
        echo "同居するので、親機のホスト名 ${ORIGIN_HOSTNAME:-?} 以外にしてください。"
    fi
    while true; do
        if [ "$allow_same" = "1" ]; then
            hostname="$(wizard_ask "このハブのホスト名" "${ORIGIN_HOSTNAME:-raspberrypi5}")"
        else
            hostname="$(wizard_ask "このハブのホスト名" "$(wizard_sibling_name "${ORIGIN_HOSTNAME:-}" raspberrypi5-2)")"
        fi
        wizard_validate_hostname "$hostname" "$origin" "$allow_same" && break
    done
    echo
    echo "----- ② 工場の Wi-Fi（このハブが工場網へ繋がる先） -----"
    while true; do
        factory_ssid="$(wizard_ask "工場の Wi-Fi 名（SSID）" "${ORIGIN_FACTORY_SSID:-$FACTORY_SSID}")"
        wizard_validate_not_empty "$factory_ssid" "工場の Wi-Fi 名" && break
    done
    echo
    echo "----- ③ このハブの工場網アドレス -----"
    echo "工場 Wi-Fi 上での、この機械の固定IPです（内蔵 wlan0）。子Pi用 AP の 10.42.0.1 ではありません。"
    while true; do
        if [ "$allow_same" = "1" ]; then
            factory_ip="$(wizard_ask "このハブの工場固定IP" "${ORIGIN_FACTORY_IP:-}")"
        else
            echo "親機の工場固定IPは ${ORIGIN_FACTORY_IP:-?} です。同居するので別のIPにしてください。"
            factory_ip="$(wizard_ask "このハブの工場固定IP")"
        fi
        wizard_validate_factory_ip "$factory_ip" "$origin" "$allow_same" && break
    done
    echo
    echo "----- ④ 工場のゲートウェイと DNS -----"
    while true; do
        factory_gw="$(wizard_ask "工場のゲートウェイ" "${ORIGIN_FACTORY_GW:-$FACTORY_GW}")"
        wizard_validate_ipv4 "$factory_gw" "ゲートウェイ" && break
    done
    while true; do
        factory_dns="$(wizard_ask "工場の DNS" "${ORIGIN_FACTORY_DNS:-$FACTORY_DNS}")"
        wizard_validate_not_empty "$factory_dns" "DNS" && break
    done
    echo
    echo "----- ⑤ Oracle（記録の書き先） -----"
    while true; do
        oracle_host="$(wizard_ask "Oracle のホスト" "${ORIGIN_ORACLE_HOST:-$ORACLE_HOST}")"
        wizard_validate_not_empty "$oracle_host" "Oracle のホスト" && break
    done
    while true; do
        oracle_port="$(wizard_ask "Oracle のポート" "${ORIGIN_ORACLE_PORT:-$ORACLE_PORT}")"
        wizard_validate_port "$oracle_port" && break
    done
    while true; do
        oracle_service="$(wizard_ask "Oracle のサービス名" "${ORIGIN_ORACLE_SERVICE:-$ORACLE_SERVICE}")"
        wizard_validate_not_empty "$oracle_service" "サービス名" && break
    done
    while true; do
        oracle_user="$(wizard_ask "Oracle のユーザ" "${ORIGIN_ORACLE_USER:-$ORACLE_USER}")"
        wizard_validate_not_empty "$oracle_user" "ユーザ" && break
    done
    while true; do
        oracle_table="$(wizard_ask "Oracle のテーブル" "${ORIGIN_ORACLE_TABLE:-$ORACLE_TABLE}")"
        wizard_validate_not_empty "$oracle_table" "テーブル" && break
    done
    echo
    echo "----- ⑥ 子Pi用ハブAPの Wi-Fi 名 -----"
    echo "子Piが選ぶ Wi-Fi の名前（SSID）です（親機は ${ORIGIN_AP_SSID:-?}）。"
    echo "①のホスト名（親機では ${ORIGIN_HOSTNAME:-?}）とは別の項目です。"
    echo "装置名 wlan1 を付ける作業ではありません。"
    while true; do
        if [ "$allow_same" = "1" ]; then
            echo "同じにすると、クローンした子がこのハブへ付きます。"
            ap_ssid="$(wizard_ask "子Pi用ハブAPの Wi-Fi名" "${ORIGIN_AP_SSID:-presence-hub}")"
            wizard_validate_ap_ssid "$ap_ssid" "$origin" 1 && break
        else
            echo "同居するので、親機のハブAP名 ${ORIGIN_AP_SSID:-?} 以外にしてください。"
            ap_ssid="$(wizard_ask "子Pi用ハブAPの Wi-Fi名" "$(wizard_sibling_name "${ORIGIN_AP_SSID:-}" presence-hub-2)")"
            wizard_validate_ap_ssid "$ap_ssid" "$origin" && break
        fi
    done
    echo
    echo "----- ⑦ パスワード（画面には出ません） -----"
    while true; do
        ap_psk="$(wizard_ask_secret "子Pi用ハブAPのパスワード（8文字以上）")"
        wizard_validate_ap_psk "$ap_psk" && break
    done
    while true; do
        oracle_pass="$(wizard_ask_secret "Oracle のパスワード（画面には出ません）")"
        if [ -n "$oracle_pass" ]; then
            break
        fi
        echo "空にはできません" >&2
    done

    echo
    echo "----- 確認 -----"
    echo "  ① このハブのホスト名         : $hostname"
    echo "  ② 工場の Wi-Fi 名            : $factory_ssid"
    echo "  ③ このハブの工場固定IP       : $factory_ip"
    echo "  ④ ゲートウェイ / DNS         : $factory_gw / $factory_dns"
    echo "  ⑤ Oracle                     : $oracle_user@$oracle_host:$oracle_port/$oracle_service"
    echo "     テーブル                   : $oracle_table"
    echo "  ⑥ 子Pi用ハブAPの Wi-Fi名     : $ap_ssid"
    echo "  ⑦ 子Pi用APパスワード         : （入力済み）"
    echo "     Oracle パスワード          : （入力済み）"
    echo "---------------"
    local yn
    read -r -p "この内容で進めますか？ [y/N]: " yn
    [[ "$yn" =~ ^[yY]$ ]] || { echo "中止しました"; return 1; }

    WIZ_FACTORY_SSID="$factory_ssid"
    WIZ_FACTORY_GW="$factory_gw"
    WIZ_FACTORY_DNS="$factory_dns"
    WIZ_ORACLE_HOST="$oracle_host"
    WIZ_ORACLE_PORT="$oracle_port"
    WIZ_ORACLE_SERVICE="$oracle_service"
    WIZ_ORACLE_USER="$oracle_user"
    WIZ_ORACLE_TABLE="$oracle_table"
    export WIZ_FACTORY_SSID WIZ_FACTORY_GW WIZ_FACTORY_DNS
    export WIZ_ORACLE_HOST WIZ_ORACLE_PORT WIZ_ORACLE_SERVICE WIZ_ORACLE_USER WIZ_ORACLE_TABLE

    local site_out="$repo/site.env"
    wizard_render_site_env "$tmpl" "$origin" "$hostname" "$factory_ip" "$ap_ssid" \
        "$allow_same" > "$site_out" || return 1
    chmod 600 "$site_out"
    chown "$user:$user" "$site_out" 2>/dev/null || true

    # shellcheck disable=SC1090
    set -a; source "$site_out"; set +a
    local oracle_key="${ORACLE_PASSWORD_VAR:-ORACLE_PASSWORD_HHC}"
    wizard_merge_secrets "$secrets_tmpl" "$repo/.kit/secrets.env" \
        "$oracle_key" "$oracle_pass" "$ap_psk" || return 1
    chown "$user:$user" "$repo/.kit/secrets.env" 2>/dev/null || true
    write_ap_join_env "$repo/.kit/ap-join.env" "$ap_ssid" "$ap_psk" "$user" || return 1

    echo
    echo "ここから管理者権限でパッケージ導入・ドライバ・コンテナを進めます。"
    if [ "$(id -u)" -ne 0 ]; then
        sudo -v || return 1
        sudo bash "$repo/scripts/bootstrap-hub.sh" || return 1
    else
        bash "$repo/scripts/bootstrap-hub.sh" || return 1
    fi

    date -Iseconds > "$marker"
    chown "$user:$user" "$marker" 2>/dev/null || true

    cat <<EOF

✅ ハブの初期設定が終わりました。

  子Pi はデスクトップの「子をこのハブへ付ける」をクリックし、質問に答えてください。
  （既存の子は名前と局番号が残ります。新しい子はそこで改名します）
      SSID : $ap_ssid
      PASS : （いま入れた AP パスワード）

  docker グループをこのデスクトップセッションへ反映するため、一度再起動してください。
  再起動しなくてもハブ自体（コンテナ・AP）はもう動いています。

Enterで閉じる
EOF
    read -r -p "" _
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
