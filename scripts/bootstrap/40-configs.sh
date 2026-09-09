#!/usr/bin/env bash
# 40-configs.sh — site.env から /etc/presence-logger/ を生成する。
#
# secrets.env はウィザードが書いた .kit/secrets.env を優先して置く。
# 既存があれば上書きしない（パスワードを消さない）。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
# shellcheck source=scripts/lib/site-env.sh
source "$REPO_DIR/scripts/lib/site-env.sh"
ETC_DIR="${ETC_DIR:-/etc/presence-logger}"

configs_render_profiles() {
    local hidden="false" d dns_lines="" sntp
    case "${FACTORY_HIDDEN:-yes}" in
        yes|true|1|YES|True) hidden="true" ;;
    esac
    IFS=',' read -ra _dns <<< "${FACTORY_DNS:-}"
    for d in "${_dns[@]}"; do
        d="${d// /}"
        [ -n "$d" ] || continue
        dns_lines+="        - \"$d\""$'\n'
    done
    sntp="${SNTP_SERVERS%% *}"
    sntp="${sntp//\"/}"
    cat <<EOF
profiles:
  ${FACTORY_SSID}:
    description: "Factory internal Wi-Fi -> ${ORACLE_SERVICE} via JDBC sidecar"
    wifi:
      psk: "\${WIFI_PSK_HIMEREAP}"
      hidden: ${hidden}
      static_ipv4:
        address: "${FACTORY_IP}"
        gateway: "${FACTORY_GW}"
        dns:
${dns_lines}    sntp:
      servers: ["${sntp}"]
    oracle:
      client_mode: "${ORACLE_CLIENT_MODE}"
      auth_mode: "${ORACLE_AUTH_MODE}"
      host: "${ORACLE_HOST}"
      port: ${ORACLE_PORT}
      service_name: "${ORACLE_SERVICE}"
      user: "${ORACLE_USER}"
      password: "\${${ORACLE_PASSWORD_VAR}}"
      table_name: "${ORACLE_TABLE}"
      upcmpflg: ${UPCMPFLG:-1}

unknown_ssid_policy: "${UNKNOWN_SSID_POLICY:-drop}"
EOF
}

configs_render_device() {
    cat <<EOF
# ハブでは detector を起動しない。station は必須キーなので親と衝突しない値。
device_id: null
station:
  sta_no1: "${PARENT_STA_NO1}"
  sta_no2: "${PARENT_STA_NO2}"
  sta_no3: "${PARENT_STA_NO3}"
EOF
}

configs_install_file() {
    local dest="$1" mode="$2" owner="${3:-root:root}"
    local tmp
    tmp="$(mktemp)"
    cat > "$tmp"
    install -m "$mode" -o "${owner%%:*}" -g "${owner##*:}" "$tmp" "$dest"
    rm -f "$tmp"
}

main() {
    site_env_require
    local user="${SUDO_USER:-$USER}"
    mkdir -p "$ETC_DIR" "$ETC_DIR/wallets" /var/lib/presence-logger /var/log/presence-logger
    chmod 0755 "$ETC_DIR" /var/lib/presence-logger /var/log/presence-logger
    chmod 0700 "$ETC_DIR/wallets"

    echo "==> profiles.yaml / device.yaml を生成"
    configs_render_profiles | configs_install_file "$ETC_DIR/profiles.yaml" 0640
    configs_render_device | configs_install_file "$ETC_DIR/device.yaml" 0644

    local f
    for f in bridge.yaml detector.yaml; do
        if [ ! -f "$ETC_DIR/$f" ] && [ -f "$REPO_DIR/config/site/$f" ]; then
            install -m 0644 "$REPO_DIR/config/site/$f" "$ETC_DIR/$f"
        fi
    done

    if [ ! -f "$ETC_DIR/upcmpflg.override" ]; then
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
        fi
        if getent group docker >/dev/null; then
            chown root:docker "$secrets" 2>/dev/null || chmod 0600 "$secrets"
        fi
    else
        echo "exists, skipped: $secrets"
    fi

    if [ -f "$REPO_DIR/scripts/install.sh" ]; then
        # timesyncd と systemd unit。configs は上で書いたので install.sh は
        # copy_if_missing で上書きしない。
        bash "$REPO_DIR/scripts/install.sh" || true
    fi
    echo "✅ /etc/presence-logger を用意しました"
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
