#!/usr/bin/env bash
# 40-configs.sh — site.env から /etc/presence-logger/ を生成する。
#
# 生成関数は stdout へ書く純関数にしてある(テストが /etc に触らずに済むため)。
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

configs_render_profiles_yaml() {
    cat <<EOF
# 自動生成: scripts/bootstrap/40-configs.sh ($SITE_ENV_PATH より)
# 手で直した場合は site.env 側も合わせること。
profiles:
  $FACTORY_SSID:
    description: "$FACTORY_SSID (生成: $HUB_HOSTNAME)"
    wifi:
      psk: "\${WIFI_PSK_HIMEREAP}"
      hidden: ${FACTORY_HIDDEN:-yes}
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

unknown_ssid_policy: "${UNKNOWN_SSID_POLICY:-hold}"
EOF
}

configs_render_device_yaml() {
    cat <<EOF
# 自動生成: scripts/bootstrap/40-configs.sh
# device_id: null は /etc/host_hostname (ホスト名)から自動取得する意味。
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

configs_missing_secret_keys() {
    local f="${1:-$ETC_DIR/secrets.env}" k
    for k in "$ORACLE_PASSWORD_VAR" WIFI_PSK_HIMEREAP WIFI_AP_PSK; do
        grep -q "^$k=" "$f" 2>/dev/null || printf '%s\n' "$k"
    done
}

main() {
    site_env_require
    install -d -m 0755 "$ETC_DIR" /var/lib/presence-logger /var/log/presence-logger
    install -d -m 0700 "$ETC_DIR/wallets"

    echo "==> profiles.yaml / device.yaml を生成"
    configs_render_profiles_yaml > "$ETC_DIR/profiles.yaml"
    configs_render_device_yaml   > "$ETC_DIR/device.yaml"
    chown root:root "$ETC_DIR/profiles.yaml" "$ETC_DIR/device.yaml"
    chmod 640 "$ETC_DIR/profiles.yaml"; chmod 644 "$ETC_DIR/device.yaml"

    echo "==> bridge.yaml / detector.yaml を配置"
    cp -n "$REPO_DIR/config/site/bridge.yaml"   "$ETC_DIR/bridge.yaml"
    cp -n "$REPO_DIR/config/site/detector.yaml" "$ETC_DIR/detector.yaml"
    chmod 644 "$ETC_DIR/bridge.yaml" "$ETC_DIR/detector.yaml"

    # docker がディレクトリとして作ってしまう前に、ファイルとして用意する。
    # install.sh はこれを作らないが docker-compose.yml はマウントする。
    local user="${SUDO_USER:-$USER}"
    [ -e "$ETC_DIR/upcmpflg.override" ] || printf '%s\n' "${UPCMPFLG:-1}" > "$ETC_DIR/upcmpflg.override"
    chown "$user:$user" "$ETC_DIR/upcmpflg.override"

    if [ ! -f "$ETC_DIR/secrets.env" ]; then
        install -m 600 /dev/null "$ETC_DIR/secrets.env"
        getent group docker >/dev/null && chown root:docker "$ETC_DIR/secrets.env"
    fi

    local missing; missing="$(configs_missing_secret_keys "$ETC_DIR/secrets.env")"
    if [ -n "$missing" ]; then
        echo
        echo "⚠ $ETC_DIR/secrets.env に次のキーがありません。値を入れてください:"
        printf '    %s=\n' $missing
        echo "  値は現行機で 'sudo cat $ETC_DIR/secrets.env' を表示して手入力すること。"
        echo "  (scp や共有フォルダを経由させない)"
    fi

    echo "==> timesyncd を設定"
    bash "$REPO_DIR/scripts/install.sh" >/dev/null || true
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
