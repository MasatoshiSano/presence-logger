#!/usr/bin/env bash
# setup-hub-wizard.sh — デスクトップの「ハブ初期設定」から呼ばれる対話セットアップ。
#
# ホスト名・工場網・AP・Oracle を順に聞く。空Enter はコピー元（USBキット）の値。
# site.env と secrets を書いて bootstrap-hub.sh を回す。
set -uo pipefail

WIZARD_REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# キットと書き出し先。テストでは tmp に向け、本番ではリポジトリそのもの。
WIZARD_WORKDIR="${WIZARD_WORKDIR:-$WIZARD_REPO_DIR}"
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
    if [[ ! "$addr" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || ! _site_env_ipv4_octets_valid "$addr"; then
        echo "固定IPの形式が不正です: $ip" >&2
        echo "  各オクテットは 0-255、先頭ゼロ不可" >&2
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
    if [[ ! "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || ! _site_env_ipv4_octets_valid "$ip"; then
        echo "${label}の形式が不正です: $ip" >&2
        echo "  各オクテットは 0-255、先頭ゼロ不可" >&2
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

# 子Pi用ハブAP名の推奨値は「いま入れたホスト名-hub」。ホスト名そのものではない。
wizard_default_ap_ssid() {
    local hostname="${1:-}"
    if [ -z "$hostname" ]; then
        printf '%s\n' "${2:-presence-hub}"
        return 0
    fi
    printf '%s-hub\n' "$hostname"
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

wizard_replace_env_key() {
    local dest="$1" key="$2" val="$3" tmp
    mkdir -p "$(dirname "$dest")"
    tmp="$(mktemp)"
    if [ -f "$dest" ]; then
        grep -vE "^${key}=" "$dest" > "$tmp" || true
    fi
    printf '%s=%s\n' "$key" "$val" >> "$tmp"
    mv "$tmp" "$dest"
    chmod 600 "$dest"
}

wizard_current_ap_ssid() {
    local repo="$1" join="$repo/.kit/ap-join.env" site="$repo/site.env"
    local ssid=""
    if [ -f "$join" ]; then
        ssid="$(grep -E '^AP_SSID=' "$join" | head -1 | cut -d= -f2-)"
    fi
    if [ -z "$ssid" ] && [ -f "$site" ]; then
        ssid="$(grep -E '^AP_SSID=' "$site" | head -1 | cut -d= -f2-)"
    fi
    printf '%s\n' "$ssid"
}

# キットの ap-join.env から /etc の WIFI_AP_PSK を直す。PSK はファイルから読む。
wizard_sync_psk_to_etc() {
    local kit_join="${1:?}"
    local etc_secrets="${2:-/etc/presence-logger/secrets.env}"
    local psk tmp
    psk="$(grep -E '^WIFI_AP_PSK=' "$kit_join" | head -1 | cut -d= -f2-)"
    psk="${psk%$'\r'}"
    if [ "${#psk}" -lt 8 ]; then
        echo "ap-join.env のパスワードが短すぎます" >&2
        return 1
    fi
    tmp="$(mktemp)"
    if [ -f "$etc_secrets" ]; then
        grep -vE '^WIFI_AP_PSK=' "$etc_secrets" > "$tmp" || true
    fi
    printf 'WIFI_AP_PSK=%s\n' "$psk" >> "$tmp"
    mkdir -p "$(dirname "$etc_secrets")"
    install -m 600 "$tmp" "$etc_secrets"
    if getent group docker >/dev/null 2>&1; then
        chown root:docker "$etc_secrets" 2>/dev/null || true
    fi
    rm -f "$tmp"
}

wizard_redo_ap_psk() {
    local repo="$1" user="$2"
    local ssid psk
    ssid="$(wizard_current_ap_ssid "$repo")"
    if [ -z "$ssid" ]; then
        echo "AP 名が読めません。site.env か .kit/ap-join.env を確認してください。" >&2
        return 1
    fi
    echo "子Pi用ハブAPの名前は ${ssid} のままです。"
    echo "パスワードを打ち間違えてクローンが付かないときに、ここだけやり直します。"
    psk="$(wizard_ask_secret "子Pi用ハブAPのパスワード（8文字以上）")" || return 1
    wizard_is_back "$psk" && return 0
    wizard_validate_ap_psk "$psk" || return 1
    write_ap_join_env "$repo/.kit/ap-join.env" "$ssid" "$psk" "$user" || return 1
    if [ -f "$repo/.kit/secrets.env" ]; then
        wizard_replace_env_key "$repo/.kit/secrets.env" WIFI_AP_PSK "$psk"
        chown "$user:$user" "$repo/.kit/secrets.env" 2>/dev/null || true
    fi
    if [ "${WIZARD_DRY_RUN:-}" = "1" ]; then
        echo "DRY-RUN: AP は再起動しません。キットのパスワードだけ書きました。"
        return 0
    fi
    if [ "$(id -u)" -ne 0 ]; then
        sudo env KIT_JOIN="$repo/.kit/ap-join.env" bash "$WIZARD_REPO_DIR/scripts/setup-hub-wizard.sh" --sync-psk-to-etc \
            || return 1
        sudo AP_FORCE=1 bash "$repo/scripts/bootstrap/50-ap.sh" || return 1
    else
        wizard_sync_psk_to_etc "$repo/.kit/ap-join.env" /etc/presence-logger/secrets.env || return 1
        AP_FORCE=1 bash "$repo/scripts/bootstrap/50-ap.sh" || return 1
    fi
    echo "AP パスワードを書き直して、子Pi用 AP を上げ直しました。"
}

WIZ_BACK='__WIZ_BACK__'

wizard_is_back() {
    [ "${1:-}" = "$WIZ_BACK" ]
}

# 漢字は不要。0 が本線。戻る / << も受け付ける。
wizard_reply_is_back() {
    case "${1:-}" in
        0|戻る|"<<") return 0 ;;
        *) return 1 ;;
    esac
}

wizard_ask() {
    local prompt="$1" default="${2:-}" reply
    if [ -n "$default" ]; then
        read -r -p "$prompt [$default] (0=戻る): " reply || return 1
    else
        read -r -p "$prompt (0=戻る): " reply || return 1
    fi
    if wizard_reply_is_back "$reply"; then
        printf '%s\n' "$WIZ_BACK"
        return 0
    fi
    if [ -n "$default" ]; then
        printf '%s\n' "${reply:-$default}"
    else
        printf '%s\n' "$reply"
    fi
}

wizard_ask_secret() {
    local prompt="$1" reply
    read -r -s -p "$prompt (0=戻る): " reply || return 1
    echo >&2
    if wizard_reply_is_back "$reply"; then
        printf '%s\n' "$WIZ_BACK"
        return 0
    fi
    printf '%s\n' "$reply"
}

main() {
    local repo="${WIZARD_WORKDIR}"
    local origin="$repo/.kit/origin.env"
    local tmpl="$repo/.kit/site.env.template"
    local secrets_tmpl="$repo/.kit/secrets.env.template"
    local marker="$repo/.kit/setup-complete"
    local user="${SUDO_USER:-$USER}"

    if wizard_already_configured "$marker"; then
        echo "既に設定済みです（$marker）。"
        echo "ホスト名や工場IPのやり直しは、そのファイルを消してからもう一度開いてください。"
        echo
        echo "  Enter : 閉じる"
        echo "  p     : 子Pi用APのパスワードだけやり直す（クローンが付かないとき）"
        read -r -p "番号または Enter: " v || return 0
        case "$v" in
            p|P)
                wizard_redo_ap_psk "$repo" "$user"
                echo "Enterで閉じる"
                read -r -p "" _ || true
                return 0
                ;;
            *)
                return 0
                ;;
        esac
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
    echo "間違えたら 0 で直前の質問に戻れます。確認では 1〜7 でその項目からやり直せます。"
    echo
    echo "  1) 親機と同時に動かす（同じ工場網。このハブの名前・工場IP・子Pi用AP名は親機と別）"
    echo "  2) 親機はもう使わない、または別の工場網"
    echo "     （コピー元と同じホスト名・工場IP・AP名でもよい）"
    echo "     クローンが自動で付くのは、AP 名とパスワードが旧ハブと同じときに限ります。"
    echo "     パスワードは USB に入っていないので、同じにするなら手で入れてください。"
    echo
    local hostname factory_ip factory_ssid factory_gw factory_dns
    local oracle_host oracle_port oracle_service oracle_user oracle_table
    local ap_ssid ap_psk oracle_pass mode allow_same=0
    local step=0 v yn needs_stop_confirm=0

    while [ "$step" -ge 0 ]; do
        case "$step" in
            0)
                v="$(wizard_ask "番号で選ぶ" "1")" || return 1
                wizard_is_back "$v" && continue
                case "$v" in
                    1) mode=1; allow_same=0; step=1 ;;
                    2) mode=2; allow_same=1; step=1 ;;
                    *) echo "1 / 2 で選んでください" >&2 ;;
                esac
                ;;
            1)
                echo
                echo "----- ① このハブのホスト名 -----"
                echo "Linux がこの Raspberry Pi を呼ぶ名前です（親機は ${ORIGIN_HOSTNAME:-?}）。"
                echo "子Piが探す Wi-Fi 名（親機では ${ORIGIN_AP_SSID:-?}）ではありません。"
                if [ "$allow_same" != "1" ]; then
                    echo "同居するので、親機のホスト名 ${ORIGIN_HOSTNAME:-?} 以外にしてください。"
                fi
                if [ "$allow_same" = "1" ]; then
                    v="$(wizard_ask "このハブのホスト名" "${ORIGIN_HOSTNAME:-raspberrypi5}")" || return 1
                else
                    v="$(wizard_ask "このハブのホスト名" "$(wizard_sibling_name "${ORIGIN_HOSTNAME:-}" raspberrypi5-2)")" || return 1
                fi
                wizard_is_back "$v" && { step=0; continue; }
                if wizard_validate_hostname "$v" "$origin" "$allow_same"; then
                    hostname="$v"
                    step=2
                fi
                ;;
            2)
                echo
                echo "----- ② 工場の Wi-Fi（このハブが工場網へ繋がる先） -----"
                v="$(wizard_ask "工場の Wi-Fi 名（SSID）" "${ORIGIN_FACTORY_SSID:-$FACTORY_SSID}")" || return 1
                wizard_is_back "$v" && { step=1; continue; }
                if wizard_validate_not_empty "$v" "工場の Wi-Fi 名"; then
                    factory_ssid="$v"
                    step=3
                fi
                ;;
            3)
                echo
                echo "----- ③ このハブの工場網アドレス -----"
                echo "工場 Wi-Fi 上での、この機械の固定IPです（内蔵 wlan0）。子Pi用 AP の 10.42.0.1 ではありません。"
                if [ "$allow_same" = "1" ]; then
                    v="$(wizard_ask "このハブの工場固定IP" "${ORIGIN_FACTORY_IP:-}")" || return 1
                else
                    echo "親機の工場固定IPは ${ORIGIN_FACTORY_IP:-?} です。同居するので別のIPにしてください。"
                    v="$(wizard_ask "このハブの工場固定IP")" || return 1
                fi
                wizard_is_back "$v" && { step=2; continue; }
                if wizard_validate_factory_ip "$v" "$origin" "$allow_same"; then
                    factory_ip="$v"
                    step=4
                fi
                ;;
            4)
                needs_stop_confirm=0
                if [ "$allow_same" = "1" ]; then
                    if [ "$hostname" = "${ORIGIN_HOSTNAME:-}" ] || \
                        [ "${factory_ip%/*}" = "${ORIGIN_FACTORY_IP%/*}" ]; then
                        needs_stop_confirm=1
                    fi
                fi
                if [ "$needs_stop_confirm" != "1" ]; then
                    step=5
                    continue
                fi
                echo
                echo "親機と同じホスト名または工場固定IPです。"
                echo "親機が同じ工場網でまだ動いていると、工場網が壊れます。"
                v="$(wizard_ask "親機は本当に止まっていますか？ y/N" "N")" || return 1
                wizard_is_back "$v" && { step=3; continue; }
                if [[ "$v" =~ ^[yY]$ ]]; then
                    step=5
                else
                    echo "番号 1（同居）にするか、別のホスト名・工場IPにしてください。" >&2
                    step=0
                fi
                ;;
            5)
                echo
                echo "----- ④ 工場のゲートウェイと DNS -----"
                v="$(wizard_ask "工場のゲートウェイ" "${ORIGIN_FACTORY_GW:-$FACTORY_GW}")" || return 1
                wizard_is_back "$v" && { step=3; continue; }
                if wizard_validate_ipv4 "$v" "ゲートウェイ"; then
                    factory_gw="$v"
                    step=6
                fi
                ;;
            6)
                v="$(wizard_ask "工場の DNS" "${ORIGIN_FACTORY_DNS:-$FACTORY_DNS}")" || return 1
                wizard_is_back "$v" && { step=5; continue; }
                if wizard_validate_not_empty "$v" "DNS"; then
                    factory_dns="$v"
                    step=7
                fi
                ;;
            7)
                echo
                echo "----- ⑤ Oracle（記録の書き先） -----"
                v="$(wizard_ask "Oracle のホスト" "${ORIGIN_ORACLE_HOST:-$ORACLE_HOST}")" || return 1
                wizard_is_back "$v" && { step=6; continue; }
                if wizard_validate_not_empty "$v" "Oracle のホスト"; then
                    oracle_host="$v"
                    step=8
                fi
                ;;
            8)
                v="$(wizard_ask "Oracle のポート" "${ORIGIN_ORACLE_PORT:-$ORACLE_PORT}")" || return 1
                wizard_is_back "$v" && { step=7; continue; }
                if wizard_validate_port "$v"; then
                    oracle_port="$v"
                    step=9
                fi
                ;;
            9)
                v="$(wizard_ask "Oracle のサービス名" "${ORIGIN_ORACLE_SERVICE:-$ORACLE_SERVICE}")" || return 1
                wizard_is_back "$v" && { step=8; continue; }
                if wizard_validate_not_empty "$v" "サービス名"; then
                    oracle_service="$v"
                    step=10
                fi
                ;;
            10)
                v="$(wizard_ask "Oracle のユーザ" "${ORIGIN_ORACLE_USER:-$ORACLE_USER}")" || return 1
                wizard_is_back "$v" && { step=9; continue; }
                if wizard_validate_not_empty "$v" "ユーザ"; then
                    oracle_user="$v"
                    step=11
                fi
                ;;
            11)
                v="$(wizard_ask "Oracle のテーブル" "${ORIGIN_ORACLE_TABLE:-$ORACLE_TABLE}")" || return 1
                wizard_is_back "$v" && { step=10; continue; }
                if wizard_validate_not_empty "$v" "テーブル"; then
                    oracle_table="$v"
                    step=12
                fi
                ;;
            12)
                echo
                echo "----- ⑥ 子Pi用ハブAPの Wi-Fi 名 -----"
                echo "子Piが選ぶ Wi-Fi の名前（SSID）です（親機は ${ORIGIN_AP_SSID:-?}）。"
                echo "①のホスト名（いま入れた値は ${hostname}）とは別の項目です。"
                echo "装置名 wlan1 を付ける作業ではありません。"
                if [ "$allow_same" = "1" ]; then
                    echo "コピー元と同じ AP 名にしても、パスワードが違うとクローンは付きません。"
                    v="$(wizard_ask "子Pi用ハブAPの Wi-Fi名" "${ORIGIN_AP_SSID:-presence-hub}")" || return 1
                    wizard_is_back "$v" && { step=11; continue; }
                    if wizard_validate_ap_ssid "$v" "$origin" 1; then
                        ap_ssid="$v"
                        step=13
                    fi
                else
                    echo "同居するので、親機のハブAP名 ${ORIGIN_AP_SSID:-?} 以外にしてください。"
                    echo "Enter なら、①のホスト名に -hub を付けた値です。"
                    v="$(wizard_ask "子Pi用ハブAPの Wi-Fi名" "$(wizard_default_ap_ssid "$hostname")")" || return 1
                    wizard_is_back "$v" && { step=11; continue; }
                    if wizard_validate_ap_ssid "$v" "$origin"; then
                        ap_ssid="$v"
                        step=13
                    fi
                fi
                ;;
            13)
                echo
                echo "----- ⑦ パスワード（画面には出ません） -----"
                if [ "$allow_same" = "1" ]; then
                    echo "クローンした子が自動で付くには、旧ハブと同じパスワードが必要です。"
                    echo "USB には入っていません。同じにするなら旧ハブの値を入れてください。"
                fi
                v="$(wizard_ask_secret "子Pi用ハブAPのパスワード（8文字以上）")" || return 1
                wizard_is_back "$v" && { step=12; continue; }
                if wizard_validate_ap_psk "$v"; then
                    ap_psk="$v"
                    step=14
                fi
                ;;
            14)
                v="$(wizard_ask_secret "Oracle のパスワード（画面には出ません）")" || return 1
                wizard_is_back "$v" && { step=13; continue; }
                if [ -n "$v" ]; then
                    oracle_pass="$v"
                    step=15
                else
                    echo "空にはできません" >&2
                fi
                ;;
            15)
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
                echo "y で進める / n で中止 / 0 で直前 / 1〜7 でその項目からやり直し"
                read -r -p "この内容で進めますか？ [y/N]: " yn || return 1
                case "$yn" in
                    y|Y) step=-1 ;;
                    0|戻る|"<<") step=14 ;;
                    1) step=1 ;;
                    2) step=2 ;;
                    3) step=3 ;;
                    4) step=5 ;;
                    5) step=7 ;;
                    6) step=12 ;;
                    7) step=13 ;;
                    *) echo "中止しました"; return 1 ;;
                esac
                ;;
            *)
                echo "内部エラー: step=$step" >&2
                return 1
                ;;
        esac
    done

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

    if [ "${WIZARD_DRY_RUN:-}" = "1" ]; then
        echo "DRY-RUN: この内容は書き込みません。確認までで終わります。"
        return 0
    fi

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

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    case "${1:-}" in
        --sync-psk-to-etc)
            wizard_sync_psk_to_etc "${KIT_JOIN:?KIT_JOIN が空です}" \
                "${ETC_SECRETS:-/etc/presence-logger/secrets.env}"
            ;;
        *)
            main "$@"
            ;;
    esac
fi
