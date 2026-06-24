#!/usr/bin/env bash
# show-recent-records.sh
# HHS001 に「実際に上がっている」直近 N 件を、この拠点の STA_NO1/2/3 で
# 絞って最新順に表示する。docker ログではなく Oracle を直接 SELECT する
# ので「本当にDBへ入ったか」の確証になる。読み取り専用・sudo 不要。
#
#   使い方:  bash show-recent-records.sh [件数=30]
#
# 仕組み:
#   - 非秘密の接続情報(host/service/user/table)は profiles.yaml から読む
#   - station(sta_no1/2/3) は profiles.yaml に上書きが無ければ device.yaml を見る
#   - Oracle パスワードは bridge コンテナの環境変数から取る(secrets.env は
#     root専用のため。pi は docker グループなので docker exec で参照可能)
#   - oracle-jdbc サイドカーの /select_recent を docker exec 経由で叩く
#     (サイドカーは presence-net 内のみで待受、ホストにポート公開していない)
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIMIT="${1:-30}"

PROFILE_NAME="${PROFILE_NAME:-taden-ot-ap}"
PROFILES_YAML="${PROFILES_YAML:-/etc/presence-logger/profiles.yaml}"
DEVICE_YAML="${DEVICE_YAML:-/etc/presence-logger/device.yaml}"
[[ -f "$PROFILES_YAML" ]] || PROFILES_YAML="$DIR/../../projects/presence-logger/config/profiles.yaml.example"
BRIDGE_CONTAINER="${BRIDGE_CONTAINER:-presence-bridge}"
JDBC_CONTAINER="${JDBC_CONTAINER:-presence-oracle-jdbc}"
SIDECAR_IN="${SIDECAR_IN:-http://127.0.0.1:8086}"
PW_ENV="${PW_ENV:-ORACLE_PASSWORD_ONPREM}"

echo "===================================================================="
echo " presence-logger 直近記録ビューア（DBを直接確認）"
echo "   テーブル: HHS001 / 直近 ${LIMIT} 件・最新順"
echo "===================================================================="

# --- 現在のSSIDを確認（未接続なら警告。DBは工場網内からしか届かない）---
SSID="$(LC_ALL=C nmcli -t -f ACTIVE,SSID dev wifi 2>/dev/null | awk -F: '$1=="yes"{print $2; exit}')"
echo "   現在のSSID: ${SSID:-(不明)}"
if [[ "$SSID" != "$PROFILE_NAME" ]]; then
    echo "   ⚠ SSID が $PROFILE_NAME ではありません。"
    echo "     先に「taden-ot-ap に接続」を実行してから開いてください。"
    echo "     （未接続でも照会は試みますが、タイムアウトする可能性があります）"
fi

# --- 非秘密の接続情報を profiles.yaml + device.yaml から読む ---
declare -A PCFG
raw=$(python3 - "$PROFILES_YAML" "$PROFILE_NAME" "$DEVICE_YAML" <<'PY'
import sys, yaml, os
profiles_path, name, device_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(profiles_path) as f:
    data = yaml.safe_load(f)
profile = (data.get("profiles") or {}).get(name) or {}
if not profile:
    print(f"ERR no profile {name}", file=sys.stderr); sys.exit(2)
oracle = profile.get("oracle") or {}
station = profile.get("station") or {}
# Fallback: profiles.yaml に station 上書きが無ければ device.yaml を読む
if not station and os.path.isfile(device_path):
    with open(device_path) as f:
        device = yaml.safe_load(f) or {}
    station = device.get("station") or {}
def emit(k, v): print(f"PCFG[{k}]={v!r}")
emit("oracle_host", oracle.get("host", ""))
emit("oracle_port", oracle.get("port", "1521"))
emit("oracle_service", oracle.get("service_name", ""))
emit("oracle_user", oracle.get("user", ""))
emit("oracle_table", oracle.get("table_name", "HF1RCM01"))
emit("sta_no1", station.get("sta_no1", ""))
emit("sta_no2", station.get("sta_no2", ""))
emit("sta_no3", station.get("sta_no3", ""))
PY
) || { echo "FAIL: profiles.yaml ($PROFILES_YAML) を読めませんでした"; exit 1; }
eval "$raw"

# station は profiles.yaml の上書き → device.yaml デフォルト の順で解決する。
# どちらにも値が無ければエラーにする（誤った全件取得を防ぐ）。
if [[ -z "${PCFG[sta_no1]}" || -z "${PCFG[sta_no2]}" || -z "${PCFG[sta_no3]}" ]]; then
    echo "FAIL: station(sta_no1/2/3) が見つかりません"
    echo "      profiles.yaml の $PROFILE_NAME に station セクションを書くか、"
    echo "      device.yaml の station にデフォルトを設定してください。"
    exit 1
fi
echo "   STA_NO  : ${PCFG[sta_no1]}/${PCFG[sta_no2]}/${PCFG[sta_no3]}"
echo "   接続先  : ${PCFG[oracle_host]}:${PCFG[oracle_port]}/${PCFG[oracle_service]}  user=${PCFG[oracle_user]}"
echo "===================================================================="

# --- Oracle パスワードを bridge コンテナ env から取得 ---
ORACLE_PW="$(docker exec "$BRIDGE_CONTAINER" printenv "$PW_ENV" 2>/dev/null)" || true
if [[ -z "$ORACLE_PW" ]]; then
    echo "FAIL: $BRIDGE_CONTAINER から $PW_ENV を取得できませんでした"
    echo "      （bridge コンテナが起動しているか確認してください: docker ps）"
    exit 1
fi

# --- リクエストボディを組み立て（URLエンコード）---
BODY="$(python3 - <<PY
import urllib.parse
print(urllib.parse.urlencode({
    "url":         "jdbc:oracle:thin:@${PCFG[oracle_host]}:${PCFG[oracle_port]}/${PCFG[oracle_service]}",
    "user":        "${PCFG[oracle_user]}",
    "password":    """${ORACLE_PW}""",
    "table_name":  "${PCFG[oracle_table]}",
    "sta_no1":     "${PCFG[sta_no1]}",
    "sta_no2":     "${PCFG[sta_no2]}",
    "sta_no3":     "${PCFG[sta_no3]}",
    "limit":       "${LIMIT}",
}))
PY
)"

# --- サイドカーへ POST（コンテナ内ループバック宛て）---
RESPONSE="$(docker exec -i "$JDBC_CONTAINER" wget -q --timeout=40 \
    --header='Content-Type: application/x-www-form-urlencoded' \
    --post-data="$BODY" -O - "$SIDECAR_IN/select_recent" 2>&1)" || {
    echo
    echo "  ❌ サイドカー($JDBC_CONTAINER)への照会に失敗しました。"
    echo "     SSID が $PROFILE_NAME か、コンテナが healthy か確認してください。"
    echo "     詳細: $RESPONSE"
    exit 1
}

printf '%s\n' "$RESPONSE" | python3 -u "$DIR/_render_recent.py"
