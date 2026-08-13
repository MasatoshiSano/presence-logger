#!/usr/bin/env bash
# deploy-child.sh — 親(このPi 5)で実行。子(Zero2)へアプリを配布する。
#
#   リポジトリ child/ を「正」として、子の ~ 直下へ rsync し、systemd を再起動する。
#   配布 → ヘルスチェック → 失敗なら自動ロールバック。
#   runtime 状態(send_target_state.json / logs 等)は一切触らない。
#
# 使い方:
#   scripts/deploy-child.sh                      # コード + systemd unit を配布（既定）
#   scripts/deploy-child.sh --with-shared-config # フリート共通設定も配布
#   scripts/deploy-child.sh --dry-run            # 何を送るかだけ表示（変更なし）
#   scripts/deploy-child.sh --no-rollback        # 失敗しても自動復元しない
#
# 機体固有設定(id_names/threshold/recognition/save/model/crop)は *配布しない*。
# 子のWeb UIで設定する端末の状態であり、配布すると現場の設定を無警告で壊すため。

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/deploy-common.sh
source "$HERE/lib/deploy-common.sh"

WITH_SHARED=0; DRY_RUN=0; ROLLBACK=1
for a in "$@"; do case "$a" in
  --with-shared-config) WITH_SHARED=1 ;;
  --code-only)   ;;  # 非推奨: 既定と同義。既存手順を壊さないため受理のみする
  --dry-run)     DRY_RUN=1 ;;
  --no-rollback) ROLLBACK=0 ;;
  -h|--help)     sed -n '2,20p' "$0"; exit 0 ;;
  *) die "unknown arg: $a" ;;
esac; done

CHILD_SRC="$REPO_DIR/child"
[ -d "$CHILD_SRC" ] || die "child/ が無い（先に取り込みが必要）: $CHILD_SRC"

# 配布対象を決める（機体固有設定は決して含めない）
files=("${CHILD_CODE_FILES[@]}")
[ "$WITH_SHARED" -eq 0 ] || files+=("${CHILD_SHARED_CONFIG_FILES[@]}")

# ローカルに全ファイルが揃っているか
for f in "${files[@]}"; do [ -f "$CHILD_SRC/$f" ] || die "child/$f が無い"; done

TS="$(date +%Y%m%d-%H%M%S)"
log "子アプリ配布 (tag=$TS, files=${#files[@]}, with_shared=$WITH_SHARED, dry_run=$DRY_RUN)"

if [ "$DRY_RUN" -eq 1 ]; then
  rsync -avn --relative "${files[@]/#/$CHILD_SRC/./}" "$CHILD_SSH:~/" 2>&1 | sed 's/^/    /'
  echo "  (systemd units: ${CHILD_UNITS[*]} を再起動予定)"
  exit 0
fi

require_child_reachable

# 1) 上書き対象 + systemd unit を退避
child_backup "$TS" "${CHILD_CODE_FILES[@]}" "${CHILD_ALL_CONFIG_FILES[@]}"
rc "for u in ${CHILD_UNITS[*]}; do sudo cp -a /etc/systemd/system/\$u.service ~/'$CHILD_BACKUP_DIR/$TS/' 2>/dev/null || true; done" || true

# 2) 配布（--relative でリポジトリ側の相対構造のまま ~ に置く。--delete は使わない=既存を消さない）
log "rsync -> $CHILD_SSH:~/"
( cd "$CHILD_SRC" && rsync -a --info=stats0 "${files[@]}" "$CHILD_SSH:~/" ) || die "rsync 失敗"
# systemd unit（内容が変わっていれば）を配置
for u in "${CHILD_UNITS[@]}"; do
  if [ -f "$CHILD_SRC/systemd/$u.service" ]; then
    rc "cat > /tmp/$u.service" < "$CHILD_SRC/systemd/$u.service"
    rsudo "install -m 644 /tmp/$u.service /etc/systemd/system/$u.service"
  fi
done
ok "配布完了"

# 3) 再起動 → 4) ヘルスチェック → 失敗なら 5) ロールバック
child_restart_units "${CHILD_UNITS[@]}"
if child_healthcheck "${CHILD_UNITS[@]}" && child_model_ready \
   && { [ "${VERIFY_E2E:-0}" != 1 ] || child_e2e_send_check; }; then
  child_prune_backups
  ok "デプロイ成功 (tag=$TS)。~/$CHILD_BACKUP_DIR/$TS に旧版を保持"
else
  if [ "$ROLLBACK" -eq 1 ]; then
    child_restore "$TS"
    for u in "${CHILD_UNITS[@]}"; do
      rc "[ -f ~/'$CHILD_BACKUP_DIR/$TS/$u.service' ]" && \
        rsudo "install -m 644 ~/$CHILD_BACKUP_DIR/$TS/$u.service /etc/systemd/system/$u.service" || true
    done
    child_restart_units "${CHILD_UNITS[@]}"
    if child_healthcheck "${CHILD_UNITS[@]}" && child_model_ready; then
      die "デプロイ失敗→ロールバック成功。旧版で稼働中。原因を調査してください"
    else
      die "デプロイ失敗→ロールバックも不健全。手動対応が必要 (~/$CHILD_BACKUP_DIR/$TS)"
    fi
  else
    die "デプロイ失敗 (--no-rollback)。~/$CHILD_BACKUP_DIR/$TS から手動復元可"
  fi
fi
