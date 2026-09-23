#!/usr/bin/env bash
# 40-configs.sh — site.env から /etc/presence-logger/ を生成する。
#
# 生成関数は stdout へ書く純関数にしてある(テストが /etc に触らずに済むため)。
# secrets.env はウィザードが書いた .kit/secrets.env を優先して置く。
# 既存があれば上書きしない（入っているパスワードを消さない）。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
ETC_DIR="${ETC_DIR:-/etc/presence-logger}"
# shellcheck source=scripts/lib/site-env.sh
source "$REPO_DIR/scripts/lib/site-env.sh"

_yaml_list() {  # "a,b" -> ["a", "b"]
    local IFS=','; local out=""
    for x in $1; do out="${out:+$out, }\"$x\""; done
    printf '[%s]' "$out"
}

# YAML 1.1 では yes/no も真偽値だが、1.2 では文字列になる。読み手を選ばない
# true/false に正規化しておく。
_yaml_bool() {
    case "${1:-}" in
        yes|true|1|YES|True|TRUE) printf 'true' ;;
        *) printf 'false' ;;
    esac
}

configs_render_profiles_yaml() {
    cat <<EOF
# 自動生成: scripts/bootstrap/40-configs.sh
#   出所: ${SITE_ENV_PATH:-site.env} / ホスト: ${HUB_HOSTNAME:-?}
# 手で直した場合は site.env 側も合わせること。
profiles:
  $FACTORY_SSID:
    description: "$FACTORY_SSID -> $ORACLE_SERVICE via JDBC sidecar"
    wifi:
      psk: "\${WIFI_PSK_HIMEREAP}"
      hidden: $(_yaml_bool "${FACTORY_HIDDEN:-yes}")
      static_ipv4:
        address: "$FACTORY_IP"
        gateway: "$FACTORY_GW"
        dns: $(_yaml_list "$FACTORY_DNS")
    sntp:
      servers: $(_yaml_list "${SNTP_SERVERS// /,}")
    oracle:
      client_mode: "$ORACLE_CLIENT_MODE"
      auth_mode: "$ORACLE_AUTH_MODE"
      host: "$ORACLE_HOST"
      port: $ORACLE_PORT
      service_name: "$ORACLE_SERVICE"
      user: "$ORACLE_USER"
      password: "\${$ORACLE_PASSWORD_VAR}"
      table_name: "$ORACLE_TABLE"
      upcmpflg: ${UPCMPFLG:-1}

# 既定は drop。hold だと、工場網の外で拾ったイベントを溜め込んだ末に
# 次の HIME-H-REAP 接続でまとめて流し込んでしまう。
unknown_ssid_policy: "${UNKNOWN_SSID_POLICY:-drop}"
EOF
}

configs_render_device_yaml() {
    cat <<EOF
# 自動生成: scripts/bootstrap/40-configs.sh
# device_id: null は /etc/hostname (ホスト名)から自動取得する意味。
#
# station は「この親自身のカメラ検知」用。HUB_MODE=1(カメラ無し)では Oracle に
# 書かれないが、bridge の必須項目なので値は要る。将来カメラを付けたときに
# そのまま使えるよう、他機・全子Piと重複しない値にしてある。
device_id: null
station:
  sta_no1: "$PARENT_STA_NO1"
  sta_no2: "$PARENT_STA_NO2"
  sta_no3: "$PARENT_STA_NO3"
EOF
}

# stdin を受けて、所有者とモードを確定させてから所定の場所へ置く。
# 途中で失敗しても半端な内容のファイルが /etc に残らない。
configs_install_file() {
    local dest="$1" mode="$2" owner="${3:-root:root}"
    local tmp
    tmp="$(mktemp)"
    cat > "$tmp"
    install -m "$mode" -o "${owner%%:*}" -g "${owner##*:}" "$tmp" "$dest"
    rm -f "$tmp"
}

# フリート管理(pi)が AP へ子を付け替えるときに読む。PSK は画面へ出さない。
configs_write_ap_join() {
    local dest="${1:-$REPO_DIR/.kit/ap-join.env}"
    local secrets_src="${2:-}"
    local psk=""
    if [ -z "$secrets_src" ]; then
        if [ -f "$REPO_DIR/.kit/secrets.env" ]; then
            secrets_src="$REPO_DIR/.kit/secrets.env"
        elif [ -f "$ETC_DIR/secrets.env" ]; then
            secrets_src="$ETC_DIR/secrets.env"
        fi
    fi
    if [ -n "$secrets_src" ] && [ -f "$secrets_src" ]; then
        psk="$(grep -E '^WIFI_AP_PSK=' "$secrets_src" | head -1 | cut -d= -f2-)"
        psk="${psk%$'\r'}"
    fi
    if [ -z "${AP_SSID:-}" ] || [ -z "$psk" ]; then
        echo "skip ap-join.env (AP_SSID or WIFI_AP_PSK missing)"
        return 0
    fi
    write_ap_join_env "$dest" "$AP_SSID" "$psk" "${SUDO_USER:-${USER:-pi}}"
}

# 揃っていないと「動くが記録が入らない」形で壊れる。起動前に名前で挙げる。
configs_missing_secret_keys() {
    local f="${1:-$ETC_DIR/secrets.env}" k
    for k in "$ORACLE_PASSWORD_VAR" WIFI_PSK_HIMEREAP WIFI_AP_PSK; do
        grep -q "^$k=" "$f" 2>/dev/null || printf '%s\n' "$k"
    done
}

main() {
    site_env_require
    local user="${SUDO_USER:-$USER}"
    install -d -m 0755 "$ETC_DIR" /var/lib/presence-logger /var/log/presence-logger
    install -d -m 0700 "$ETC_DIR/wallets"

    echo "==> profiles.yaml / device.yaml を生成"
    configs_render_profiles_yaml | configs_install_file "$ETC_DIR/profiles.yaml" 0640
    configs_render_device_yaml   | configs_install_file "$ETC_DIR/device.yaml" 0644

    echo "==> bridge.yaml / detector.yaml を配置"
    local f
    for f in bridge.yaml detector.yaml; do
        if [ ! -f "$ETC_DIR/$f" ] && [ -f "$REPO_DIR/config/site/$f" ]; then
            install -m 0644 "$REPO_DIR/config/site/$f" "$ETC_DIR/$f"
        fi
    done

    # docker がディレクトリとして作ってしまう前に、ファイルとして用意する。
    # install.sh はこれを作らないが docker-compose.yml はマウントする。
    if [ ! -e "$ETC_DIR/upcmpflg.override" ]; then
        printf '%s\n' "${UPCMPFLG:-1}" > "$ETC_DIR/upcmpflg.override"
        chown "$user:$user" "$ETC_DIR/upcmpflg.override"
        chmod 0644 "$ETC_DIR/upcmpflg.override"
    fi

    local secrets="$ETC_DIR/secrets.env"
    if [ ! -f "$secrets" ]; then
        if [ -f "$REPO_DIR/.kit/secrets.env" ]; then
            echo "==> ウィザードの secrets を配置"
            install -m 0600 "$REPO_DIR/.kit/secrets.env" "$secrets"
        elif [ -f "$REPO_DIR/.kit/secrets.env.template" ]; then
            echo "==> secrets のひな型を配置（値の不足は後で埋めてください）"
            install -m 0600 "$REPO_DIR/.kit/secrets.env.template" "$secrets"
        elif [ -f "$REPO_DIR/config/secrets.env.example" ]; then
            install -m 0600 "$REPO_DIR/config/secrets.env.example" "$secrets"
        else
            install -m 0600 /dev/null "$secrets"
        fi
        if getent group docker >/dev/null; then
            chown root:docker "$secrets" 2>/dev/null || chmod 0600 "$secrets"
        fi
    else
        echo "exists, skipped: $secrets"
    fi

    local missing; missing="$(configs_missing_secret_keys "$secrets")"
    if [ -n "$missing" ]; then
        echo
        echo "⚠ $secrets に次のキーがありません。値を入れてください:"
        # shellcheck disable=SC2086
        printf '    %s=\n' $missing
        echo "  値は現行機で 'sudo cat $ETC_DIR/secrets.env' を表示して手入力すること。"
        echo "  (scp や共有フォルダを経由させない)"
        echo
    fi

    configs_write_ap_join

    if [ -f "$REPO_DIR/scripts/install.sh" ]; then
        # timesyncd と systemd unit。configs は上で書いたので install.sh は
        # copy_if_missing で上書きしない。
        echo "==> timesyncd / systemd unit を設定"
        bash "$REPO_DIR/scripts/install.sh" >/dev/null || true
    fi
    echo "✅ /etc/presence-logger を用意しました"
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
