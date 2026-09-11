#!/usr/bin/env bash
# setup-hub-wizard.sh — デスクトップの「ハブ初期設定」から呼ばれる対話セットアップ。
#
# ホスト名・固定IP・AP名・APパスワード・Oracleパスワードを順に聞き、
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
    local name="$1" origin="$2"
    if ! site_env_valid_hostname "$name"; then
        echo "ホスト名に使えない文字が含まれています: $name" >&2
        echo "  英小文字・数字・ハイフンのみ（先頭末尾はハイフン不可）" >&2
        return 1
    fi
    site_env_load_origin "$origin" || return 1
    if [ "$name" = "${ORIGIN_HOSTNAME:-}" ]; then
        echo "親機と同じホスト名です: $name" >&2
        echo "  共存するには別の名前にしてください" >&2
        return 1
    fi
}

wizard_validate_factory_ip() {
    local ip="$1" origin="$2" addr
    addr="${ip%/*}"
    if [[ ! "$addr" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
        echo "固定IPの形式が不正です: $ip" >&2
        return 1
    fi
    site_env_load_origin "$origin" || return 1
    if [ "$addr" = "${ORIGIN_FACTORY_IP%/*}" ]; then
        echo "親機と同じ固定IPです: $addr" >&2
        echo "  工場網で衝突します。情シスへ申請した別のIPを入れてください" >&2
        return 1
    fi
}

wizard_validate_ap_ssid() {
    local ssid="$1" origin="$2"
    if [ -z "$ssid" ]; then
        echo "AP 名が空です" >&2
        return 1
    fi
    site_env_load_origin "$origin" || return 1
    if [ "$ssid" = "${ORIGIN_AP_SSID:-}" ]; then
        echo "親機と同じ AP 名です: $ssid" >&2
        echo "  子Pi がどちらのハブに付くか不定になります" >&2
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
    # shellcheck disable=SC1090
    set -a; source "$tmpl"; set +a
    site_env_load_origin "$origin" || return 1
    wizard_validate_hostname "$hostname" "$origin" || return 1
    wizard_validate_factory_ip "$factory_ip" "$origin" || return 1
    wizard_validate_ap_ssid "$ap_ssid" "$origin" || return 1

    HUB_HOSTNAME="$hostname"
    HUB_MODE=1
    local hint="${FACTORY_IP:-$ORIGIN_FACTORY_IP}"
    FACTORY_IP="$(wizard_normalize_factory_ip "$factory_ip" "$hint")"
    AP_SSID="$ap_ssid"
    AP_GW_IP="${AP_GW_IP:-10.42.0.1}"
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

    echo "この Raspberry Pi を子Pi専用ハブにします。"
    echo "親機 ${ORIGIN_HOSTNAME:-?} はそのまま運転します。値を衝突させないでください。"
    echo

    local hostname factory_ip ap_ssid ap_psk oracle_pass
    while true; do
        hostname="$(wizard_ask "ホスト名" "presence-hub-2")"
        wizard_validate_hostname "$hostname" "$origin" && break
    done
    while true; do
        echo "親機の固定IPは ${ORIGIN_FACTORY_IP:-?} です。別のIPを入れてください。"
        factory_ip="$(wizard_ask "工場網の固定IP")"
        wizard_validate_factory_ip "$factory_ip" "$origin" && break
    done
    while true; do
        echo "親機の AP 名は ${ORIGIN_AP_SSID:-?} です。別の名前にしてください。"
        ap_ssid="$(wizard_ask "ドングルの AP 名" "${hostname}")"
        wizard_validate_ap_ssid "$ap_ssid" "$origin" && break
    done
    while true; do
        ap_psk="$(wizard_ask_secret "AP のパスワード（8文字以上・画面には出ません）")"
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
    echo "  ホスト名     : $hostname"
    echo "  固定IP       : $factory_ip"
    echo "  AP 名        : $ap_ssid"
    echo "  AP パスワード: （入力済み）"
    echo "  Oracle パス  : （入力済み）"
    echo "---------------"
    local yn
    read -r -p "この内容で進めますか？ [y/N]: " yn
    [[ "$yn" =~ ^[yY]$ ]] || { echo "中止しました"; return 1; }

    local site_out="$repo/site.env"
    wizard_render_site_env "$tmpl" "$origin" "$hostname" "$factory_ip" "$ap_ssid" \
        > "$site_out" || return 1
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

  新しい子Pi は次の AP に繋いでから、デスクトップの「フリート管理」で追加してください。
  親機に付いている子を移すときは、登録ウィザードではなく
  「他のハブから引き継ぐ」を使ってください（ホスト名と局番号が残ります）。
      SSID : $ap_ssid
      PASS : （いま入れた AP パスワード）

  docker グループをこのデスクトップセッションへ反映するため、一度再起動してください。
  再起動しなくてもハブ自体（コンテナ・AP）はもう動いています。

Enterで閉じる
EOF
    read -r -p "" _
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
