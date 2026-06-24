#!/usr/bin/env bash
# disconnect-taden-ot-ap.sh
# 検知を停止し、taden-ot-ap を切断する。
# 切断後は別の SSID には自動で繋ぎ直さない（NetworkManager の autoconnect 任せ）。
set -uo pipefail

if [[ $EUID -ne 0 ]]; then
    exec sudo bash "$0" "$@"
fi

HOLD=0
[[ "${1:-}" == "--hold" ]] && HOLD=1

CONN_NAME="${CONN_NAME:-taden-ot-ap}"

say(){ printf '%s\n' "$*"; }
finish(){ [[ "$HOLD" == 1 ]] && { echo; read -rp 'Enterキーで閉じる... ' _; }; exit "${1:-0}"; }

# コンテナを停止（detector=カメラ取得停止、土台 bridge/oracle-jdbc/mosquitto も停止）。
# inbox(SQLite) は永続化されるので、未送信分は次回「接続」時にまとめて送られる。
if docker compose --project-directory /home/pi/projects/presence-logger stop >/dev/null 2>&1; then
    say "■ コンテナを停止しました（detector / bridge / oracle-jdbc / mosquitto）"
fi

if nmcli connection down "$CONN_NAME" >/dev/null 2>&1; then
    say "✅ $CONN_NAME を切断しました"
else
    say "⚠ $CONN_NAME は既に切断状態でした"
fi

ACTUAL="$(LC_ALL=C nmcli -t -f ACTIVE,SSID dev wifi | awk -F: '$1=="yes"{print $2; exit}')"
say "  現在の接続: ${ACTUAL:-(なし)}"
finish 0
