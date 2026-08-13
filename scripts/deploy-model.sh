#!/usr/bin/env bash
# deploy-model.sh — 親(このPi 5)で実行。子(Zero2)の IMX500 モデルを更新/切替する。
#
#   モデルの「正」= リポジトリ models/<name>/<version>/{network.rpk,labels.txt,packerOut.zip}
#   子の ~/<name>/ へ rsync し、必要なら model_config.json を差し替え、picamera を再起動。
#   バージョン切替・ロールバックは実質「指し先を戻して再起動」だけ。
#
# 使い方:
#   scripts/deploy-model.sh --list                         # 手元の model store を一覧
#   scripts/deploy-model.sh signal_tower 20260422          # 指定版を配布して有効化
#   scripts/deploy-model.sh signal_tower latest            # 最新(名前順で最後)を配布
#   scripts/deploy-model.sh --dry-run signal_tower 20260422
#
# モデルファイルは 5MB 程度。親AP経由 rsync で数秒。

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/deploy-common.sh
source "$HERE/lib/deploy-common.sh"

MODEL_STORE="$REPO_DIR/models"
MODEL_FILES=(network.rpk labels.txt)
OPTIONAL_FILES=(packerOut.zip)   # 再パック用の元データ。無くても配布・実行には支障ない
DRY_RUN=0

args=()
for a in "$@"; do case "$a" in
  --list)    ls -1 "$MODEL_STORE" 2>/dev/null | while read -r n; do
               echo "$n:"; ls -1 "$MODEL_STORE/$n" 2>/dev/null | sed 's/^/  /'; done; exit 0 ;;
  --dry-run) DRY_RUN=1 ;;
  -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
  *) args+=("$a") ;;
esac; done

[ "${#args[@]}" -eq 2 ] || die "使い方: deploy-model.sh <name> <version|latest>  (--list で確認)"
NAME="${args[0]}"; VER="${args[1]}"
[ -d "$MODEL_STORE/$NAME" ] || die "未知のモデル: $NAME (--list)"
if [ "$VER" = "latest" ]; then
  VER="$(ls -1 "$MODEL_STORE/$NAME" | sort | tail -1)"
  [ -n "$VER" ] || die "$NAME にバージョンが無い"
  log "latest = $VER"
fi
SRC="$MODEL_STORE/$NAME/$VER"
[ -d "$SRC" ] || die "$NAME/$VER が無い (--list)"
for f in "${MODEL_FILES[@]}"; do [ -f "$SRC/$f" ] || die "$SRC/$f が無い"; done
for f in "${OPTIONAL_FILES[@]}"; do [ -f "$SRC/$f" ] || warn "$SRC/$f が無い(再パック用。配布は続行)"; done

TS="$(date +%Y%m%d-%H%M%S)"
log "モデル配布: $NAME/$VER -> 子 ~/$NAME/ (tag=$TS)"

if [ "$DRY_RUN" -eq 1 ]; then
  rsync -avn "$SRC/" "$CHILD_SSH:~/$NAME/" 2>&1 | sed 's/^/    /'
  echo "  model_config.json.model_type を '$NAME' に更新 & picamera 再起動 予定"
  exit 0
fi

require_child_reachable

# 1) 現行モデル + model_config を退避
child_backup "$TS" "$NAME" model_config.json

# 2) モデル配布（対象ディレクトリ内は同期。--delete で古い断片を残さない）
rc "mkdir -p ~/$NAME"
rsync -a --delete "$SRC/" "$CHILD_SSH:~/$NAME/" || die "モデル rsync 失敗"

# 3) 有効モデルを model_config.json で指す（絶対パスは /home/pi/<name>/…）
rc "python3 - <<PY
import json,os
p=os.path.expanduser('~/model_config.json')
c=json.load(open(p)) if os.path.exists(p) else {}
c.update({'model_type':'$NAME',
          'network':os.path.expanduser('~/$NAME/network.rpk'),
          'labels':os.path.expanduser('~/$NAME/labels.txt')})
json.dump(c,open(p,'w'))
print('model_config ->',c)
PY" || die "model_config 更新失敗"
ok "配布 & 有効化"

# 4) picamera のみ再起動 → 5) ヘルスチェック + readiness(層2) → 失敗なら復元
#    ※期待モデル($NAME)が実際に ready で有効化されたかまで確認する
child_restart_units picamera
if child_healthcheck picamera && child_model_ready "$NAME" \
   && { [ "${VERIFY_E2E:-0}" != 1 ] || child_e2e_send_check; }; then
  child_prune_backups
  ok "モデル更新成功: $NAME/$VER"
else
  warn "picamera が不健全 → モデルをロールバック"
  child_restore "$TS"
  child_restart_units picamera
  { child_healthcheck picamera && child_model_ready; } \
    && die "モデル更新失敗→ロールバック成功。旧モデルで稼働中" \
    || die "モデル更新失敗→ロールバックも不健全。手動対応 (~/$CHILD_BACKUP_DIR/$TS)"
fi
