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

# 1オクテットが 0-255 の範囲で、かつ先頭ゼロを持たないかを判定する。
# "0" 単体は正しい値として許可するが、"00" "008" のような桁揃えのゼロ埋めは
# bash の算術展開で8進数と解釈され(例: 008 はエラー、022 は 18 と誤読)、
# 検証全体を無力化する原因になるため、ここで先に弾く。
_site_env_valid_octet() {
    [[ "$1" =~ ^(0|[1-9][0-9]{0,2})$ ]] || return 1
    [ "$1" -le 255 ]
}

# ドット区切りIPv4アドレス(プレフィックス無し)が、4オクテットとも
# _site_env_valid_octet を満たすかだけを判定する。全体の形式(桁数・区切り数)は
# 呼び出し側の正規表現で先に見ているため、ここではオクテットの中身だけを見る。
_site_env_ipv4_octets_valid() {
    local IFS=.
    local -a o=($1)
    [ "${#o[@]}" -eq 4 ] || return 1
    local part
    for part in "${o[@]}"; do
        _site_env_valid_octet "$part" || return 1
    done
    return 0
}

# IPv4アドレス(ドット区切り4オクテット)を32bit整数に変換する。
# 呼び出し前に _site_env_ipv4_octets_valid で弾いている前提だが、"10#" で
# 基数10を明示し、万一ゼロ埋めが紛れ込んでも8進数として誤読させない
# (多重防御。ここだけに頼って検証を省略しないこと)。
_site_env_ip_to_int() {
    local IFS=.
    local -a o=($1)
    echo $(( (10#${o[0]} << 24) + (10#${o[1]} << 16) + (10#${o[2]} << 8) + 10#${o[3]} ))
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

    # FACTORY_IP は「アドレス4オクテット / プレフィックス」の形。ここでの正規表現は
    # 桁数の粗いふるい(digits.digits.digits.digits/digits)に過ぎず、300 や 008 の
    # ような値もここは通る。実際の範囲・先頭ゼロは後段でオクテット単位・
    # プレフィックス単位に分けて検証し、どちらが悪いかを別々に報告する
    # (両方とも「CIDR 形式で指定してください」と言うと、オペレーターは
    # どこを直せばいいか分からない)。
    local factory_ip_valid=1
    if [[ "$FACTORY_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}$ ]]; then
        local f_addr="${FACTORY_IP%/*}" f_prefix="${FACTORY_IP#*/}"
        local f_addr_ok=1 f_prefix_ok=1
        if ! _site_env_ipv4_octets_valid "$f_addr"; then
            echo "FACTORY_IP のIPアドレス部分が不正です(各オクテットは 0-255、先頭ゼロ不可): $FACTORY_IP" >&2
            errors=$((errors + 1))
            f_addr_ok=0
        fi
        if [[ "$f_prefix" =~ ^(0|[1-9][0-9]?)$ ]] && [ "$f_prefix" -le 32 ]; then
            :
        else
            echo "FACTORY_IP のプレフィックス長が不正です(0-32、先頭ゼロ不可): $FACTORY_IP" >&2
            errors=$((errors + 1))
            f_prefix_ok=0
        fi
        [ "$f_addr_ok" -eq 1 ] && [ "$f_prefix_ok" -eq 1 ] && factory_ip_valid=0
    else
        echo "FACTORY_IP は CIDR 形式で指定してください (例 172.22.13.18/24): $FACTORY_IP" >&2
        errors=$((errors + 1))
    fi

    # AP_GW_IP は同一サブネット判定の算術にそのまま使われるため、FACTORY_IP と
    # 同じ強さでオクテットを検証する。これまでここは未検証で、不正値がそのまま
    # _site_env_same_subnet に渡っていた。
    local ap_gw_ip_valid=1
    if [[ "$AP_GW_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] && _site_env_ipv4_octets_valid "$AP_GW_IP"; then
        ap_gw_ip_valid=0
    else
        echo "AP_GW_IP は IPv4 アドレス形式で指定してください(各オクテットは 0-255、先頭ゼロ不可、例 10.42.0.1): $AP_GW_IP" >&2
        errors=$((errors + 1))
    fi

    local inv="${CHILDREN_CONF:-$SITE_ENV_REPO_DIR/fleet/children.conf}"
    if [ -f "$inv" ] && grep -v '^[[:space:]]*#' "$inv" | grep -qFx "$HUB_HOSTNAME"; then
        echo "HUB_HOSTNAME が子Piと重複しています: $HUB_HOSTNAME" >&2
        echo "  ホスト名は device_id と MQTT client_id を決めるため、重複すると相互切断する" >&2
        errors=$((errors + 1))
    fi

    # FACTORY_IP か AP_GW_IP のどちらかが不正なまま同一サブネット判定に渡すと、
    # 意味のない比較結果(たまたま衝突/非衝突どちらにも転びうる)になるか、
    # 不正な値が原因で bash の算術構文エラーが生の内部エラーとして stderr に
    # 漏れる。どちらの不正もエラーは既に報告済みなので、ここでは黙って
    # 後続チェックを飛ばす。
    if [ "$factory_ip_valid" -eq 0 ] && [ "$ap_gw_ip_valid" -eq 0 ] && \
        _site_env_same_subnet "$AP_GW_IP" "${FACTORY_IP%/*}" "${FACTORY_IP#*/}"; then
        echo "AP_GW_IP が工場網と同一サブネットです: $AP_GW_IP / $FACTORY_IP" >&2
        errors=$((errors + 1))
    fi

    if ! site_env_reject_origin; then
        errors=$((errors + 1))
    fi

    [ "$errors" -eq 0 ]
}

site_env_valid_hostname() {
    local n="${1:-}"
    [ -n "$n" ] || return 1
    [ "${#n}" -le 63 ] || return 1
    [[ "$n" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]]
}

site_env_origin_path() {
    printf '%s\n' "${ORIGIN_ENV_PATH:-$SITE_ENV_REPO_DIR/.kit/origin.env}"
}

site_env_load_origin() {
    local path="${1:-$(site_env_origin_path)}"
    if [ ! -f "$path" ]; then
        echo "origin.env が見つかりません: $path" >&2
        return 1
    fi
    # shellcheck disable=SC1090
    set -a; source "$path"; set +a
}

site_env_reject_origin() {
    local origin="${1:-$(site_env_origin_path)}"
    [ -f "$origin" ] || return 0
    site_env_load_origin "$origin" || return 1
    # 親機を置き換える／別工場では、コピー元と同じホスト名・IP・AP名を許す。
    if [ "${ORIGIN_REPLACE:-}" = "1" ]; then
        return 0
    fi
    local errors=0
    if [ -n "${ORIGIN_HOSTNAME:-}" ] && [ "${HUB_HOSTNAME:-}" = "$ORIGIN_HOSTNAME" ]; then
        echo "HUB_HOSTNAME が親機と同じです: $HUB_HOSTNAME" >&2
        echo "  親機と共存するには別のホスト名にしてください" >&2
        errors=$((errors + 1))
    fi
    local new_ip="${FACTORY_IP%/*}" old_ip="${ORIGIN_FACTORY_IP%/*}"
    if [ -n "${ORIGIN_FACTORY_IP:-}" ] && [ -n "$new_ip" ] && [ "$new_ip" = "$old_ip" ]; then
        echo "FACTORY_IP が親機と同じです: $new_ip" >&2
        echo "  工場網で衝突します。情シスへ申請した別の固定IPを入れてください" >&2
        errors=$((errors + 1))
    fi
    if [ -n "${ORIGIN_AP_SSID:-}" ] && [ "${AP_SSID:-}" = "$ORIGIN_AP_SSID" ]; then
        if [ "${ORIGIN_ALLOW_SAME_AP:-}" != "1" ]; then
            echo "AP_SSID が親機と同じです: $AP_SSID" >&2
            echo "  子Pi がどちらのハブに付くか不定になります。別の AP 名にしてください" >&2
            echo "  親機を置き換える／別工場なら site.env に ORIGIN_ALLOW_SAME_AP=1" >&2
            errors=$((errors + 1))
        fi
    fi
    [ "$errors" -eq 0 ]
}

site_env_require() {
    site_env_load "${1:-}" || exit 1
    site_env_validate || exit 1
    site_env_reject_origin || exit 1
}

# フリート管理が pi で読める AP 参加情報。/etc の secrets.env は root:docker 0600
# なので、子の引き継ぎでは使えない。PSK はここにだけ置き、画面へは出さない。
write_ap_join_env() {
    local dest="${1:?}"
    local ssid="${2:-${AP_SSID:-}}"
    local psk="${3:-${WIFI_AP_PSK:-}}"
    local owner="${4:-${SUDO_USER:-${USER:-pi}}}"
    if [ -z "$ssid" ] || [ -z "$psk" ]; then
        echo "AP_SSID または WIFI_AP_PSK が空のため ap-join.env を書けません" >&2
        return 1
    fi
    mkdir -p "$(dirname "$dest")"
    local old_umask
    old_umask="$(umask)"
    umask 077
    printf 'AP_SSID=%s\nWIFI_AP_PSK=%s\n' "$ssid" "$psk" > "$dest"
    umask "$old_umask"
    chown "$owner:$owner" "$dest" 2>/dev/null || true
    chmod 600 "$dest"
}
