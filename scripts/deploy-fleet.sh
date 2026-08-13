#!/usr/bin/env bash
# deploy-fleet.sh — 親で実行。インベントリの子へ 1台ずつ順に配布する。
#
#   各子への配布・ヘルスチェック・自動ロールバックは既存の deploy-child.sh /
#   deploy-model.sh がそのまま担う。本スクリプトは「順に回して、失敗したら止める」だけ。
#   既定は fail-fast: 1台失敗した時点で以降へは配らない（不良リリースをフリート全体へ
#   広げないため）。--keep-going で継続できる。
#
# 使い方:
#   scripts/deploy-fleet.sh app                          # 全子へアプリ配布
#   scripts/deploy-fleet.sh app --only zero2,zero2b      # 対象を絞る（canary先行）
#   scripts/deploy-fleet.sh app --dry-run                # 送る差分の確認のみ
#   scripts/deploy-fleet.sh app --keep-going             # 失敗しても後続へ進む
#   scripts/deploy-fleet.sh model signal_tower 20260422  # 全子へモデル配布
#
# その他のフラグ(--with-shared-config / --no-rollback 等)はそのまま子スクリプトへ渡す。
# 対象一覧は fleet/children.conf（FLEET_INVENTORY で上書き可）。

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/deploy-common.sh
source "$HERE/lib/deploy-common.sh"

CHILD_SCRIPT="${FLEET_CHILD_SCRIPT:-$HERE/deploy-child.sh}"
MODEL_SCRIPT="${FLEET_MODEL_SCRIPT:-$HERE/deploy-model.sh}"

[ $# -gt 0 ] || die "使い方: deploy-fleet.sh app|model [引数...]（-h で詳細）"
MODE="$1"; shift
case "$MODE" in
  app|model) ;;
  # 冒頭コメントを最初の空行まで出す(行番号を固定しない)。
  -h|--help) sed -n '2,/^$/p' "$0"; exit 0 ;;
  *) die "不明なモード: $MODE（app または model）" ;;
esac

MODEL_ARGS=()
if [ "$MODE" = model ]; then
  [ $# -ge 2 ] || die "model モードは <name> <version> が必要"
  MODEL_ARGS=("$1" "$2"); shift 2
fi

ONLY=""; KEEP_GOING=0; PASSTHRU=()
while [ $# -gt 0 ]; do
  case "$1" in
    --only)       shift; ONLY="${1:-}"; [ -n "$ONLY" ] || die "--only に値がありません" ;;
    --only=*)     ONLY="${1#--only=}" ;;
    --keep-going) KEEP_GOING=1 ;;
    *)            PASSTHRU+=("$1") ;;
  esac
  shift
done

# 対象ホストを決める
# プロセス置換 < <(...) だと fleet_read_inventory の die がサブシェル止まりになり、
# インベントリ不正時に「0台へ配って成功」で終わってしまう。必ず一度変数で受ける。
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
    HOSTS+=("$w")
  done
  [ ${#HOSTS[@]} -gt 0 ] || die "--only に有効なホストがありません"
else
  HOSTS=("${ALL_HOSTS[@]}")
fi

if [ "$MODE" = model ]; then
  CMD=("$MODEL_SCRIPT" "${MODEL_ARGS[@]}")
else
  CMD=("$CHILD_SCRIPT")
fi
[ ${#PASSTHRU[@]} -eq 0 ] || CMD+=("${PASSTHRU[@]}")

log "フリート配布 mode=$MODE 対象=${#HOSTS[@]}台 (${HOSTS[*]}) fail_fast=$((1-KEEP_GOING))"

OK_HOSTS=(); NG_HOSTS=()
idx=0
for h in "${HOSTS[@]}"; do
  idx=$((idx+1))
  log "===== [$h] ($idx/${#HOSTS[@]}) ====="
  # CHILD_AP_IP は毎回空にする。前の子のIPを持ち越すと別の子をヘルスチェックし得る。
  if CHILD_SSH="$h" CHILD_AP_IP="" "${CMD[@]}"; then
    OK_HOSTS+=("$h")
    ok "[$h] 完了"
  else
    NG_HOSTS+=("$h")
    if [ "$KEEP_GOING" -eq 1 ]; then
      warn "[$h] 失敗。--keep-going のため継続する"
    else
      warn "[$h] 失敗。以降の配布を中止する（継続するには --keep-going）"
      break
    fi
  fi
done

echo
log "結果: 成功 ${#OK_HOSTS[@]} / 失敗 ${#NG_HOSTS[@]} / 対象 ${#HOSTS[@]}"
[ ${#OK_HOSTS[@]} -eq 0 ] || ok "成功: ${OK_HOSTS[*]}"
if [ ${#NG_HOSTS[@]} -gt 0 ]; then
  warn "失敗: ${NG_HOSTS[*]}"
  skipped=$(( ${#HOSTS[@]} - ${#OK_HOSTS[@]} - ${#NG_HOSTS[@]} ))
  [ "$skipped" -le 0 ] || warn "未実施: ${skipped}台"
  exit 1
fi
ok "全 ${#HOSTS[@]} 台へ配布完了"
