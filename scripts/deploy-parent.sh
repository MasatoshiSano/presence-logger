#!/usr/bin/env bash
# deploy-parent.sh — 親(このPi 5)自身を git ref ベースで更新する。
#
#   （既定: ローカル ref を）checkout → docker compose 再構築 → ヘルスチェック
#   → 失敗なら直前の ref へ自動ロールバック。
#
# 使い方:
#   scripts/deploy-parent.sh v1.2.3         # ローカルの tag/ブランチ/commit で更新
#   scripts/deploy-parent.sh --fetch v1.2.3 # 接続できる時だけ GitHub から取得してから更新
#   scripts/deploy-parent.sh --list         # ローカルのタグ一覧
#   scripts/deploy-parent.sh --dry-run v1.2.3
#
# 前提: 親は普段 社内ネットで GitHub 非接続。親は稼働ツリー兼gitツリーなので、
#       親上で `git tag vX` → checkout でネット無しに完結する。GitHub は接続時のみの
#       バックアップ/取り込み(--fetch)。systemd は `docker compose up -d` を叩くだけで冪等。

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/deploy-common.sh
source "$HERE/lib/deploy-common.sh"

cd "$REPO_DIR"

DRY_RUN=0; REF=""; FETCH=0
for a in "$@"; do case "$a" in
  --list)    git tag --sort=-creatordate | head -20; exit 0 ;;
  --dry-run) DRY_RUN=1 ;;
  --fetch)   FETCH=1 ;;   # GitHub に接続できる時だけ明示的に fetch する
  -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
  -*) die "unknown arg: $a" ;;
  *) REF="$a" ;;
esac; done
[ -n "$REF" ] || die "更新先 ref を指定してください (tag/branch/commit)。--list で一覧"

# 親は普段 社内ネットで GitHub 非接続。既定はローカル ref のみで完結する。
# --fetch 指定時だけ、短いタイムアウト付きで取得を試みる（固まらないように）。
maybe_fetch() {
  [ "$FETCH" -eq 1 ] || { log "GitHub fetch はスキップ（ローカル ref を使用。取得するなら --fetch）"; return 0; }
  log "GitHub から fetch を試行（最大 ${GIT_FETCH_TIMEOUT:-15}s）"
  timeout "${GIT_FETCH_TIMEOUT:-15}" git fetch --tags --quiet origin \
    && ok "fetch 完了" || warn "fetch 失敗/タイムアウト → ローカル ref で続行"
}

PREV="$(git rev-parse --abbrev-ref HEAD)"
[ "$PREV" = "HEAD" ] && PREV="$(git rev-parse HEAD)"   # detached なら commit を覚える
log "親更新: $PREV -> $REF"

if [ "$DRY_RUN" -eq 1 ]; then
  maybe_fetch
  echo "  checkout: $REF"; git --no-pager log --oneline -1 "$REF" 2>/dev/null || warn "$REF をローカルで解決できない（--fetch が必要?）"
  echo "  then: docker compose up -d --build"
  exit 0
fi

# クリーンでないと checkout が壊れるので確認
if ! git diff --quiet || ! git diff --cached --quiet; then
  die "作業ツリーに未コミット変更があります。commit/stash してから実行してください"
fi

maybe_fetch
git rev-parse --verify --quiet "$REF^{commit}" >/dev/null \
  || die "ref '$REF' がローカルに無い。親上で 'git tag $REF' で作るか、接続時に --fetch で取得してください"
git checkout --quiet "$REF" || die "checkout $REF 失敗"

parent_healthcheck() {
  log "親ヘルスチェック"
  # compose 定義済みサービスが全て running か
  local defined running
  defined="$(docker compose config --services 2>/dev/null | sort)"
  running="$(docker compose ps --services --status running 2>/dev/null | sort)"
  if [ -n "$defined" ] && [ "$defined" != "$running" ]; then
    warn "running でないサービスがある:"; comm -23 <(echo "$defined") <(echo "$running") | sed 's/^/    -/' >&2
    return 1
  fi
  # mosquitto(1883) が応答するか
  timeout 5 bash -c 'exec 3<>/dev/tcp/127.0.0.1/1883' 2>/dev/null \
    || { warn "mosquitto :1883 が応答しない"; return 1; }
  ok "親サービス健全"
}

log "docker compose up -d --build"
if docker compose up -d --build && parent_healthcheck; then
  ok "親デプロイ成功: $REF"
else
  warn "親デプロイ失敗 → $PREV へロールバック"
  git checkout --quiet "$PREV" || die "ロールバック checkout 失敗（手動対応）"
  docker compose up -d --build || die "ロールバック compose 失敗（手動対応）"
  parent_healthcheck \
    && die "デプロイ失敗→ロールバック成功。$PREV で稼働中" \
    || die "デプロイ失敗→ロールバックも不健全。手動対応が必要"
fi
