#!/usr/bin/env bash
# fleet-status.sh — 親で実行。インベントリの子の稼働状況と STA_NO 割当を一覧する。
#
#   各子から サービス状態 / モデル readiness / STA_NO割当(id_names_config.json) を集め、
#   最後に子を跨いだ STA_NO の重複を検査する。
#   Oracle の MERGE キーは device_id を含まないため、STA_NO が重複するとレコードが
#   無警告で欠落する。子を増やす前後に必ず通すこと。
#
# 使い方:
#   scripts/fleet-status.sh                # 全子
#   scripts/fleet-status.sh --only zero2   # 対象を絞る
#
# 終了コード(scripts/lib/sta_no_report.py と同じ契約):
#   0 = 全機体を検査できて重複なし
#   1 = STA_NO 重複あり(最も実行可能な合図なので、検査不能な子があっても優先する)
#   2 = 検査しきれていない(到達できない子がある / 割当を読めない子がある)
# ※ 2 を 0 と混同しないこと。「安全」ではなく「確かめられていない」を意味する。

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/deploy-common.sh
source "$HERE/lib/deploy-common.sh"

ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --only)    shift; ONLY="${1:-}"; [ -n "$ONLY" ] || die "--only に値がありません" ;;
    # 空の値を黙って無視しない(空白形式と同じ扱いにする)。
    --only=*)  ONLY="${1#--only=}"; [ -n "$ONLY" ] || die "--only に値がありません" ;;
    # 冒頭コメントを最初の空行まで出す(行番号を固定しない)。
    -h|--help) sed -n '2,/^$/p' "$0"; exit 0 ;;
    *)         die "unknown arg: $1" ;;
  esac
  shift
done

# プロセス置換 < <(...) だと fleet_read_inventory の die がサブシェル止まりになり、
# インベントリ不正時に「0台を検査して正常」で終わってしまう。必ず一度変数で受ける。
inventory_out="$(fleet_read_inventory)" || exit 1
mapfile -t ALL_HOSTS <<< "$inventory_out"
HOSTS=()
if [ -n "$ONLY" ]; then
  IFS=',' read -r -a want <<< "$ONLY"
  for w in "${want[@]}"; do
    w="$(printf '%s' "$w" | tr -d '[:space:]')"
    [ -n "$w" ] || continue
    printf '%s\n' "${ALL_HOSTS[@]}" | grep -qx -- "$w" \
      || die "--only の '$w' はインベントリにありません: $FLEET_INVENTORY"
    # --only は利用者入力から組み立てるため fleet_read_inventory の重複除去を
    # 通らない。同じ子を二重に扱わないよう、ここでも重複を落とす。
    if [ ${#HOSTS[@]} -gt 0 ] && printf '%s\n' "${HOSTS[@]}" | grep -qx -- "$w"; then
      continue
    fi
    HOSTS+=("$w")
  done
else
  HOSTS=("${ALL_HOSTS[@]}")
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
UNREACHABLE=0

log "フリート状態 (${#HOSTS[@]}台)"
printf '  %-16s %-8s %-10s %-8s %-12s %s\n' HOST PICAMERA WEB MQTT MODEL READY
for h in "${HOSTS[@]}"; do
  # 子ごとに IP を空へ戻す。child_resolve_ap_ip はグローバルへ書き込み、
  # 「解決済み」と「利用者指定」を区別できないため、戻さないと2台目以降が
  # 1台目のIPを使い回して *別の子* を見に行く。
  CHILD_SSH="$h"; CHILD_AP_IP=""
  if ! rc 'echo ok' >/dev/null 2>&1; then
    printf '  %-16s %s\n' "$h" "到達できません(SSH失敗)"
    UNREACHABLE=1
    printf '{}' > "$TMP/$h.json"
    continue
  fi
  child_resolve_ap_ip >/dev/null 2>&1 || true

  pica="$(rc 'systemctl is-active picamera.service' 2>/dev/null || echo unknown)"
  web="$(rc 'systemctl is-active web_server.service' 2>/dev/null || echo unknown)"
  mqtt="$(rc 'systemctl is-active child-csv-to-mqtt.service' 2>/dev/null || echo unknown)"
  url="http://$CHILD_AP_IP:$CHILD_WEB_PORT"
  # model_type と status は両方 /model_status に入っている。1回の取得で足りる。
  # /current_model からは読まないこと: 実機 zero2 では network/labels しか返らず
  # model_type が無いため、常に不明扱いになる。
  st="$(curl -sf -m 5 "$url/model_status" 2>/dev/null || true)"
  model="$(printf '%s' "$st" | grep -o '"model_type"[[:space:]]*:[[:space:]]*"[^"]*"' \
           | sed 's/.*"\([^"]*\)"$/\1/' || true)"
  ready="$(printf '%s' "$st" | grep -o '"status"[[:space:]]*:[[:space:]]*"[^"]*"' \
           | sed 's/.*"\([^"]*\)"$/\1/' || true)"
  printf '  %-16s %-8s %-10s %-8s %-12s %s\n' \
    "$h" "$pica" "$web" "$mqtt" "${model:-?}" "${ready:-?}"

  # STA_NO 割当を回収（取得できなければ空扱い）
  rc 'cat ~/id_names_config.json' 2>/dev/null > "$TMP/$h.json" || printf '{}' > "$TMP/$h.json"
done

# 全機体の割当を1つのJSONにまとめ、重複検査へ渡す
echo
python3 - "$TMP" "${HOSTS[@]}" <<'PY' > "$TMP/all.json"
import json, pathlib, sys
tmp = pathlib.Path(sys.argv[1])
out = {}
for host in sys.argv[2:]:
    try:
        data = json.loads((tmp / f"{host}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    # 最上位が dict でも id_names の中身が dict とは限らない({"id_names": "壊れた文字列"}
    # のような子が実在しうる)。両方を確かめないと sta_no_report.py へ壊れた形を渡してしまう。
    ids = data.get("id_names") if isinstance(data, dict) else None
    out[host] = ids if isinstance(ids, dict) else {}
print(json.dumps(out))
PY

DUP_RC=0
python3 "$HERE/lib/sta_no_report.py" < "$TMP/all.json" || DUP_RC=$?

if [ "$UNREACHABLE" -ne 0 ]; then
  warn "到達できない子があります(この子は検査できていません)"
fi

# 1(重複あり)は最も実行可能な合図なので、検査不能な子があっても優先する。
if [ "$DUP_RC" -eq 1 ]; then
  exit 1
fi
# 到達できない子、または割当を読めない子があれば「確かめられていない」。
if [ "$DUP_RC" -ne 0 ] || [ "$UNREACHABLE" -ne 0 ]; then
  exit 2
fi
exit 0
