#!/usr/bin/env bash
# pipeline-monitor.sh
# 子Pi→MQTT→bridge(record_inbox)→Oracle を1画面で追う監視TUIを起動する。
# 既存の「記録モニタ」(自Piカメラ検知) とは別物: こちらは子Pi経路とDB段階が対象。
#   使い方:  bash pipeline-monitor.sh
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 前提チェック（欠けていても分かりやすく落ちるように）
if ! command -v mosquitto_sub >/dev/null 2>&1; then
    echo "mosquitto_sub がありません。次で導入してください:"
    echo "    sudo apt-get install -y mosquitto-clients"
    exit 1
fi

SSID="$(nmcli -t -f ACTIVE,SSID dev wifi 2>/dev/null | awk -F: '$1=="yes"{print $2; exit}')"
echo "===================================================================="
echo " presence パイプライン監視（子Pi→MQTT→Oracle）"
echo "   現在のSSID : ${SSID:-(不明)}"
echo "   ①子Pi別受信  ②MQTT生ログ  ③record_inbox  ④Oracleテーブル"
echo "   [r] Oracle即時更新   [q] 終了"
echo "   ※④は SSID が工場網(既定 HIME-H-REAP)のときだけ表示されます"
echo "===================================================================="

# パッケージの親を PYTHONPATH に載せて -m 実行。
PYTHONPATH="$DIR" exec python3 -m pipeline_monitor
