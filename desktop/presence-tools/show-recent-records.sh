#!/usr/bin/env bash
# show-recent-records.sh
# HHC001 に「実際に上がっている」直近 N 件を、この拠点の STA_NO1/2/3 で
# 絞って最新順に表示する。docker ログではなく Oracle を直接 SELECT する
# ので「本当にDBへ入ったか」の確証になる。読み取り専用・sudo 不要。
#
#   使い方:  bash show-recent-records.sh [件数=30]
#
# 仕組み:
#   - 非秘密の接続情報(host/service/user/table/station)は profiles.yaml から読む
#   - Oracle パスワードは bridge コンテナの環境変数から取る(secrets.env は
#     root専用のため。pi は docker グループなので docker exec で参照可能)
#   - oracle-jdbc サイドカーの /select_recent を docker exec 経由で叩く
#     (サイドカーは presence-net 内のみで待受、ホストにポート公開していない)
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIMIT="${1:-30}"

PROFILE_NAME="${PROFILE_NAME:-HIME-H-REAP}"
PROFILES_YAML="${PROFILES_YAML:-/etc/presence-logger/profiles.yaml}"
[[ -f "$PROFILES_YAML" ]] || PROFILES_YAML="$DIR/../../projects/presence-logger/config/profiles.yaml.example"
BRIDGE_CONTAINER="${BRIDGE_CONTAINER:-presence-bridge}"
JDBC_CONTAINER="${JDBC_CONTAINER:-presence-oracle-jdbc}"
SIDECAR_IN="${SIDECAR_IN:-http://127.0.0.1:8086}"
PW_ENV="${PW_ENV:-ORACLE_PASSWORD_HHC}"

echo "===================================================================="
echo " presence-logger 直近記録ビューア（DBを直接確認）"
echo "   テーブル: HHC001 / 直近 ${LIMIT} 件・最新順"
echo "===================================================================="

# --- 現在のSSIDを確認（未接続なら警告。DBは工場網内からしか届かない）---
# dual-WiFi構成では wlan1 が常時 presence-hub AP として「active」に見えるため、
# `nmcli dev wifi` の先頭yes行を拾うと AP 側を誤検出する（接続済みでも
# 「未接続」と警告してしまう）。工場網は wlan0 固定なので wlan0 だけを見る。
SSID="$(iwgetid -r "${WAN_IFACE:-wlan0}" 2>/dev/null)"
echo "   現在のSSID: ${SSID:-(不明)}"
if [[ "$SSID" != "$PROFILE_NAME" ]]; then
    echo "   ⚠ SSID が $PROFILE_NAME ではありません。"
    echo "     先に「HIME-H-REAP に接続」を実行してから開いてください。"
    echo "     （未接続でも照会は試みますが、タイムアウトする可能性があります）"
fi

# --- 非秘密の接続情報を profiles.yaml から読む ---
declare -A PCFG
raw=$(python3 - "$PROFILES_YAML" "$PROFILE_NAME" <<'PY'
import sys, yaml
profiles_path, name = sys.argv[1], sys.argv[2]
with open(profiles_path) as f:
    data = yaml.safe_load(f)
profile = (data.get("profiles") or {}).get(name) or {}
if not profile:
    print(f"ERR no profile {name}", file=sys.stderr); sys.exit(2)
oracle = profile.get("oracle") or {}
station = profile.get("station") or {}
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

# profiles.yaml の station は「Enter を押したときの既定値」として使う。空でもエラーに
# しない: `*` を明示すればその列で絞らない（全拠点横断）のが意図的な動作になったため。

# --- 対話式フィルタ入力（Enter=既定/絞らない、* =その列を絞らない）---
echo
echo "  フィルタ条件を入力（Enter で既定 / 詳細は各行の括弧）"
read -rp "  STA_NO1（Enter=${PCFG[sta_no1]} / *=すべて）: " IN_S1
read -rp "  STA_NO2（Enter=${PCFG[sta_no2]} / *=すべて）: " IN_S2
read -rp "  STA_NO3（Enter=${PCFG[sta_no3]} / *=すべて）: " IN_S3
read -rp "  期間 from YYYYMMDDhhmmss（Enter=指定なし）: " IN_FROM
read -rp "  期間 to   YYYYMMDDhhmmss（Enter=指定なし）: " IN_TO
read -rp "  T1_STATUS（Enter=すべて / 例 1 や 3）: " IN_T1
read -rp "  件数（Enter=${LIMIT}、最大200）: " IN_LIMIT
read -rp "  2099センチネル行を含める（Enter=含める / n=除外）: " IN_SENT

resolve_sta() {  # $1=入力 $2=profile既定
    case "$1" in
        "")  printf '%s' "$2" ;;   # Enter → profile値
        "*") printf '' ;;          # * → 絞らない(空)
        *)   printf '%s' "$1" ;;   # 明示指定
    esac
}
F_S1="$(resolve_sta "$IN_S1" "${PCFG[sta_no1]}")"
F_S2="$(resolve_sta "$IN_S2" "${PCFG[sta_no2]}")"
F_S3="$(resolve_sta "$IN_S3" "${PCFG[sta_no3]}")"
F_FROM="$IN_FROM"
F_TO="$IN_TO"
F_T1="$IN_T1"
[[ -n "$IN_LIMIT" ]] && LIMIT="$IN_LIMIT"
# 既定は「含める」。スモーク行(2099)を書いたのに読み戻せないのが困るため。
case "$IN_SENT" in
    [nN]*) F_SENT=""; SENT_LABEL="除外" ;;
    *)     F_SENT="1"; SENT_LABEL="含む" ;;
esac

echo "===================================================================="
echo "   絞込: STA=${F_S1:-*}/${F_S2:-*}/${F_S3:-*}  期間=${F_FROM:-…}..${F_TO:-…}  T1=${F_T1:-すべて}  件数=${LIMIT}  2099=${SENT_LABEL}"
echo "   接続先: ${PCFG[oracle_host]}:${PCFG[oracle_port]}/${PCFG[oracle_service]}  user=${PCFG[oracle_user]}"
echo "===================================================================="

# --- Oracle パスワードを bridge コンテナ env から取得 ---
# DRY_RUN では docker にも触らず、組み立てたボディを出して終わる（プロンプトと
# POST フィールドの対応を、DBもコンテナも無い場所で検証できるようにするため）。
if [[ "${DRY_RUN:-0}" == "1" ]]; then
    ORACLE_PW="DRYRUN"
    BODY_DUMP=1
else
    ORACLE_PW="$(docker exec "$BRIDGE_CONTAINER" printenv "$PW_ENV" 2>/dev/null)" || true
    if [[ -z "$ORACLE_PW" ]]; then
        echo "FAIL: $BRIDGE_CONTAINER から $PW_ENV を取得できませんでした"
        echo "      （bridge コンテナが起動しているか確認してください: docker ps）"
        exit 1
    fi
fi

# --- リクエストボディを組み立て（URLエンコード。空欄の任意フィルタは送らない）---
#
# 対話入力(STA_NO/期間/T1)は *絶対に* Python ソースへ文字列展開しない。展開すると
# `x", "sta_no2": "y` のような入力が Python の文字列リテラルを抜けて任意コード実行に
# なる。値は環境変数で、パスワードは stdin で渡し、ソースは固定リテラルに保つ。
BODY="$(printf '%s' "$ORACLE_PW" | \
    O_HOST="${PCFG[oracle_host]}" O_PORT="${PCFG[oracle_port]}" \
    O_SVC="${PCFG[oracle_service]}" O_USER="${PCFG[oracle_user]}" \
    O_TABLE="${PCFG[oracle_table]}" O_LIMIT="$LIMIT" \
    F_S1="$F_S1" F_S2="$F_S2" F_S3="$F_S3" \
    F_FROM="$F_FROM" F_TO="$F_TO" F_T1="$F_T1" F_SENT="$F_SENT" \
    python3 -c '
import os, sys, urllib.parse
e = os.environ
fields = {
    "url": "jdbc:oracle:thin:@{}:{}/{}".format(e["O_HOST"], e["O_PORT"], e["O_SVC"]),
    "user": e["O_USER"],
    "password": sys.stdin.read(),
    "table_name": e["O_TABLE"],
    "limit": e["O_LIMIT"],
}
for key, var in (("sta_no1", "F_S1"), ("sta_no2", "F_S2"), ("sta_no3", "F_S3"),
                 ("mk_date_from", "F_FROM"), ("mk_date_to", "F_TO"),
                 ("t1_status", "F_T1"), ("include_sentinel", "F_SENT")):
    val = e.get(var, "")
    if val:
        fields[key] = val
print(urllib.parse.urlencode(fields))
')"

if [[ "${BODY_DUMP:-0}" == "1" ]]; then
    echo "[DRY_RUN] POST body fields:"
    printf '%s\n' "$BODY" | tr '&' '\n'
    exit 0
fi

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
