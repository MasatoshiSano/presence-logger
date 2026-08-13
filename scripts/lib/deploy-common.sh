# shellcheck shell=bash
# deploy-common.sh — 親→子/親自身のデプロイ共通ロジック。
# 各 deploy-*.sh から `source` して使う。単体実行はしない。
#
# 設計方針（このリポジトリ固有の現実に合わせている）:
#   - 親(Pi 5)  : git + docker compose。デプロイ = tag checkout + compose 再構築。
#   - 子(Zero2) : git も docker も無い素ファイル + systemd。RAM 416MB。
#                 デプロイ = 親から rsync でファイル配布 + systemctl restart。
#   - どちらも「配布 → ヘルスチェック → 失敗なら自動ロールバック」を徹底する。

set -euo pipefail

# ---- 定数（実機構成に一致。variables で上書き可）----------------------------
REPO_DIR="${REPO_DIR:-/home/pi/projects/presence-logger}"
CHILD_SSH="${CHILD_SSH:-zero2}"          # ~/.ssh/config の Host エントリ
# 子IP。空 = デプロイ時に子自身から取得する(DHCP動的割当のため固定値を持たない)。
# 明示指定した場合はそれを優先する。
CHILD_AP_IP="${CHILD_AP_IP:-}"
CHILD_WEB_PORT="${CHILD_WEB_PORT:-8080}" # web_server.py の PORT
CHILD_BACKUP_DIR="${CHILD_BACKUP_DIR:-.deploy-backups}" # 子の ~ 配下
KEEP_BACKUPS="${KEEP_BACKUPS:-5}"

# 子アプリの「正」= リポジトリ child/ 配下。~ 直下(flat)へ配る。
# runtime 状態(send_target_state.json / logs 等)は *絶対に* 触らない → 一覧に入れない。
CHILD_CODE_FILES=(Picamera.py web_server.py child-csv-to-mqtt.py index.html)

# フリート共通の設定。全機体で同じ値であるべきものだけを置く。
# 子のWeb UIからも編集できるため、配布は --with-shared-config 指定時のみ(既定は配らない)。
CHILD_SHARED_CONFIG_FILES=(status_code_config.json send_target_config.json)

# 機体固有の設定。子のWeb UI(:8080)でオペレーターが設定する *端末の状態* であり、
# 配布すると現場の設定を無警告で破壊する。どのフラグでも配布しない。
#   id_names_config.json  : region_id -> STA_NO1..3 の割当。子ごとに必ず異なる。
#                           Oracle の MERGE キーは device_id を含まないため、重複すると
#                           レコードが無警告で欠落する(oracle_client.py の MERGE 条件参照)。
#   threshold/recognition : カメラ個体ごとの検出調整・画角
#   save_config           : 保存トグル(現場の一時設定)
#   model_config          : モデル割当。deploy-model.sh が管理する。
#   crop_config           : child/*.py から参照なし(未使用の可能性。削除判断は別途)
CHILD_DEVICE_OWNED_FILES=(
  id_names_config.json threshold_config.json recognition_config.json
  save_config.json model_config.json crop_config.json
)

# バックアップ対象。配布しないファイルも退避しておく(復旧手段は維持する)。
CHILD_ALL_CONFIG_FILES=("${CHILD_SHARED_CONFIG_FILES[@]}" "${CHILD_DEVICE_OWNED_FILES[@]}")
CHILD_UNITS=(picamera web_server)

# ---- ログ -------------------------------------------------------------------
_c() { printf '\033[%sm' "$1"; }   # color helper
log()  { echo -e "$(_c '1;34')==>$(_c 0) $*"; }
ok()   { echo -e "$(_c '1;32')  OK$(_c 0)  $*"; }
warn() { echo -e "$(_c '1;33')  !!$(_c 0)  $*" >&2; }
die()  { echo -e "$(_c '1;31')FAIL$(_c 0) $*" >&2; exit 1; }

# ---- 子への SSH ラッパ ------------------------------------------------------
rc()   { timeout "${SSH_TIMEOUT:-30}" ssh "$CHILD_SSH" "$@"; }        # remote command
rsudo(){ timeout "${SSH_TIMEOUT:-30}" ssh "$CHILD_SSH" "sudo $*"; }   # remote sudo

# ---- 子IPの解決 -------------------------------------------------------------
# 子の実IPを子自身から取得する。親APのDHCPでIPが変わっても、常に正しい相手を
# ヘルスチェックできる。インベントリに固定IPを持たせないための土台でもある。
child_resolve_ap_ip() {
  [ -z "$CHILD_AP_IP" ] || { ok "子IP(指定値): $CHILD_AP_IP"; return 0; }
  local ip=""
  ip="$(rc 'hostname -I' 2>/dev/null | tr ' ' '\n' \
        | grep -E '^[0-9]+(\.[0-9]+){3}$' | head -n1)" || true
  [ -n "$ip" ] || die "子($CHILD_SSH)のIPv4アドレスを取得できません"
  CHILD_AP_IP="$ip"
  ok "子IP(子から取得): $CHILD_AP_IP"
}

# ---- 事前チェック -----------------------------------------------------------
require_child_reachable() {
  log "子($CHILD_SSH) 到達確認"
  rc 'echo ok >/dev/null' || die "$CHILD_SSH に SSH できません"
  child_resolve_ap_ip
  local free_mb; free_mb=$(rc "df -Pm \$HOME | awk 'NR==2{print \$4}'") || free_mb=0
  [ "${free_mb:-0}" -gt 100 ] || die "子のディスク空きが少なすぎます (${free_mb}MB)"
  ok "到達OK / 空き ${free_mb}MB"
}

# ---- バックアップ / ロールバック -------------------------------------------
# 上書き対象の *現行ファイル* だけを子側に退避する。戻り値=バックアップのタグ(ts)。
child_backup() {
  local ts="$1"; shift
  local files=("$@")
  local dst="$CHILD_BACKUP_DIR/$ts"
  # 存在するものだけ cp（--parents で相対パス保持）。無ければ後で「新規」扱い。
  rc "set -e; mkdir -p '$dst'; cd \$HOME; for f in ${files[*]}; do [ -e \"\$f\" ] && cp -a --parents \"\$f\" '$dst/' || true; done; echo '$ts' > '$dst/.tag'" \
    || die "子のバックアップに失敗"
  ok "子バックアップ作成: ~/$dst"
}

child_restore() {
  local ts="$1"
  local src="$CHILD_BACKUP_DIR/$ts"
  warn "ロールバック: ~/$src を復元"
  rc "set -e; cd \$HOME; [ -d '$src' ] || { echo 'no backup'; exit 1; }; cp -a '$src'/. \$HOME/ 2>/dev/null; rm -f \$HOME/.tag" \
    || die "ロールバック復元に失敗（手動確認が必要）"
}

child_prune_backups() {
  rc "cd \$HOME/'$CHILD_BACKUP_DIR' 2>/dev/null && ls -1dt */ 2>/dev/null | tail -n +$((KEEP_BACKUPS+1)) | xargs -r rm -rf" || true
}

# ---- 子サービス操作 ---------------------------------------------------------
child_restart_units() {
  local units=("$@")
  log "子サービス再起動: ${units[*]}"
  rsudo "systemctl daemon-reload"
  for u in "${units[@]}"; do rsudo "systemctl restart $u.service" || die "restart $u 失敗"; done
}

# ---- 子ヘルスチェック -------------------------------------------------------
# 即時ゲート: サービス active + 安定(crash-loop でない) + web_server が応答。
# 返り値 0=健全, 1=不健全。※MQTT実レコードは interval=600s で遅すぎるのでゲートには使わない。
child_healthcheck() {
  local units=("$@"); [ ${#units[@]} -gt 0 ] || units=("${CHILD_UNITS[@]}")
  local stable_wait="${HEALTH_STABLE_WAIT:-6}"

  log "子ヘルスチェック (安定待ち ${stable_wait}s)"
  for u in "${units[@]}"; do
    rc "systemctl is-active --quiet $u.service" || { warn "$u が active でない"; return 1; }
  done
  sleep "$stable_wait"
  for u in "${units[@]}"; do
    rc "systemctl is-active --quiet $u.service" || { warn "$u が起動直後に落ちた(crash-loop?)"; return 1; }
  done

  # web_server を含むときだけ HTTP 応答確認（親→子AP経由）
  if printf '%s\n' "${units[@]}" | grep -qx web_server; then
    curl -sf -m 5 "http://$CHILD_AP_IP:$CHILD_WEB_PORT/" -o /dev/null \
      || { warn "web_server :$CHILD_WEB_PORT が応答しない"; return 1; }
    ok "web_server :$CHILD_WEB_PORT 応答OK"
  fi
  ok "子サービス健全: ${units[*]}"
  return 0
}

# 層2: 推論パイプラインの readiness を確認（実トラフィック不要・決定論的）。
#   /model_status が "ready" になるまでポーリング（最大 MODEL_READY_WAIT 秒）。
#   $1 に期待 model_type を渡すと /current_model と一致するかも確認する。
child_model_ready() {
  local expect="${1:-}"
  local wait_max="${MODEL_READY_WAIT:-30}"
  local url="http://$CHILD_AP_IP:$CHILD_WEB_PORT"
  local i st="" mt=""
  log "推論 readiness 確認 (/model_status, 最大 ${wait_max}s${expect:+, expect=$expect})"
  for ((i=0; i<wait_max; i+=2)); do
    st="$(curl -sf -m 5 "$url/model_status" 2>/dev/null || true)"
    printf '%s' "$st" | grep -q '"status"[[:space:]]*:[[:space:]]*"ready"' && break
    sleep 2
  done
  printf '%s' "$st" | grep -q '"status"[[:space:]]*:[[:space:]]*"ready"' \
    || { warn "model_status が ready にならない: ${st:-<no response>}"; return 1; }
  if [ -n "$expect" ]; then
    mt="$(curl -sf -m 5 "$url/current_model" 2>/dev/null || true)"
    printf '%s' "$mt" | grep -q "\"model_type\"[[:space:]]*:[[:space:]]*\"$expect\"" \
      || { warn "有効モデルが期待と不一致 (expect=$expect): ${mt:-<no response>}"; return 1; }
  fi
  ok "推論 readiness OK (${expect:-model} ready)"
  return 0
}

# 層3(任意): E2E データ経路確認。/send_logs_now で1回送信し /send_target_status を見る。
#   600s待たずに「送信が通るか」を確認できる。VERIFY_E2E=1 のときだけ呼ぶ想定。
child_e2e_send_check() {
  local url="http://$CHILD_AP_IP:$CHILD_WEB_PORT"
  log "E2E 送信確認 (/send_logs_now)"
  curl -sf -m 20 "$url/send_logs_now" -o /dev/null \
    || { warn "/send_logs_now 呼び出し失敗"; return 1; }
  sleep 3
  local ss; ss="$(curl -sf -m 5 "$url/send_target_status" 2>/dev/null || true)"
  # 成功/success のみを合格とする（"送信失敗" を誤って合格にしないため 送信 単体は使わない）
  printf '%s' "$ss" | grep -qiE '"last_result"[[:space:]]*:[[:space:]]*"[^"]*(成功|success|ok)' \
    || { warn "送信結果が確認できない/失敗: ${ss:-<no response>}"; return 1; }
  ok "E2E 送信OK"
  return 0
}
