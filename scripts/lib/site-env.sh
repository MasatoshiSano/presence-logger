#!/usr/bin/env bash
# site-env.sh — 機体固有値(site.env)の読込と検証。
#
# 機体固有値の取り違えは「動くが壊れている」形で現れる(固定IP衝突・STA_NO重複)。
# 起動前にここで弾くのが唯一の防波堤なので、検証は厳しめにしてある。
#
# 使い方:  source scripts/lib/site-env.sh; site_env_require

SITE_ENV_REPO_DIR="${SITE_ENV_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

SITE_ENV_REQUIRED=(
    HUB_HOSTNAME HUB_MODE
    FACTORY_SSID FACTORY_IP FACTORY_GW FACTORY_DNS FACTORY_SUBNETS SNTP_SERVERS
    ORACLE_CLIENT_MODE ORACLE_AUTH_MODE ORACLE_HOST ORACLE_PORT ORACLE_SERVICE
    ORACLE_USER ORACLE_TABLE ORACLE_PASSWORD_VAR
    PARENT_STA_NO1 PARENT_STA_NO2 PARENT_STA_NO3
    AP_IF AP_SSID AP_GW_IP AP_BAND AP_CHANNEL
    HOME_SSID ADMIN_SSID
)

site_env_load() {
    local path="${1:-$SITE_ENV_REPO_DIR/site.env}"
    if [ ! -f "$path" ]; then
        echo "site.env が見つかりません: $path" >&2
        echo "  site.env.example をコピーして作成してください" >&2
        return 1
    fi
    # shellcheck disable=SC1090
    set -a; source "$path"; set +a
    SITE_ENV_PATH="$path"
}

# IPv4アドレス(ドット区切り4オクテット)を32bit整数に変換する。
_site_env_ip_to_int() {
    local IFS=.
    local -a o=($1)
    echo $(( (o[0] << 24) + (o[1] << 16) + (o[2] << 8) + o[3] ))
}

# 2つのIPアドレスが、指定プレフィックス長で同一サブネットかを判定する。
# 先頭3オクテット比較(/24決め打ち)では /16 などより広いネットワークで
# 見逃しが起こる(172.22.5.1 は 172.22.0.0/16 の内側だが先頭3オクテットは
# 不一致になる)ため、プレフィックス長からマスクを作って比較する。
_site_env_same_subnet() {
    local ip1="$1" ip2="$2" prefix="$3"
    local mask=$(( prefix == 0 ? 0 : (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF ))
    local i1 i2
    i1=$(_site_env_ip_to_int "$ip1")
    i2=$(_site_env_ip_to_int "$ip2")
    [ $(( i1 & mask )) -eq $(( i2 & mask )) ]
}

site_env_validate() {
    local errors=0 v
    for v in "${SITE_ENV_REQUIRED[@]}"; do
        if [ -z "${!v:-}" ]; then
            echo "必須項目が未設定です: $v" >&2
            errors=$((errors + 1))
        fi
    done
    [ "$errors" -gt 0 ] && return 1

    local factory_ip_valid=1
    if [[ "$FACTORY_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}$ ]]; then
        factory_ip_valid=0
    else
        echo "FACTORY_IP は CIDR 形式で指定してください (例 172.22.13.18/24): $FACTORY_IP" >&2
        errors=$((errors + 1))
    fi

    local inv="${CHILDREN_CONF:-$SITE_ENV_REPO_DIR/fleet/children.conf}"
    if [ -f "$inv" ] && grep -v '^[[:space:]]*#' "$inv" | grep -qFx "$HUB_HOSTNAME"; then
        echo "HUB_HOSTNAME が子Piと重複しています: $HUB_HOSTNAME" >&2
        echo "  ホスト名は device_id と MQTT client_id を決めるため、重複すると相互切断する" >&2
        errors=$((errors + 1))
    fi

    # FACTORY_IP がCIDR形式で通っていない場合、プレフィックス部分に
    # IPアドレス全体が渡ってしまい bash の算術構文エラーになる。
    # CIDRエラーは既に報告済みなので、ここでは黙って後続チェックを飛ばす。
    if [ "$factory_ip_valid" -eq 0 ] && \
        _site_env_same_subnet "$AP_GW_IP" "${FACTORY_IP%/*}" "${FACTORY_IP#*/}"; then
        echo "AP_GW_IP が工場網と同一サブネットです: $AP_GW_IP / $FACTORY_IP" >&2
        errors=$((errors + 1))
    fi

    [ "$errors" -eq 0 ]
}

site_env_require() {
    site_env_load "${1:-}" || exit 1
    site_env_validate || exit 1
}
