#!/usr/bin/env bash
# setup-autostart.sh
# presence-logger を「再起動で必ず復活」させる（systemd 正規化）。
#
# 既存 unit /etc/systemd/system/presence-logger.service の WorkingDirectory が
# /opt/presence-logger（未配置）を指しているため効いていない。これを実ツリー
# (/home/pi/projects/presence-logger) に drop-in で向け直し、enable する。
#
#   実行（1回だけ）:  sudo bash setup-autostart.sh
#   元に戻す:        sudo rm -r /etc/systemd/system/presence-logger.service.d \
#                    && sudo systemctl daemon-reload && sudo systemctl disable presence-logger.service
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "root で実行してください:  sudo bash $0" >&2
    exit 1
fi

UNIT="presence-logger.service"
REPO_DIR="${REPO_DIR:-/home/pi/projects/presence-logger}"
DROPIN_DIR="/etc/systemd/system/${UNIT}.d"

# --- 前提チェック ---
if ! systemctl cat "$UNIT" >/dev/null 2>&1; then
    echo "FAIL: $UNIT が存在しません（scripts/install.sh で unit 配置が必要）" >&2
    exit 1
fi
if [[ ! -f "$REPO_DIR/docker-compose.yml" ]]; then
    echo "FAIL: $REPO_DIR/docker-compose.yml が見つかりません" >&2
    exit 1
fi

echo "==> [1/4] WorkingDirectory + 起動時 detector 停止 の drop-in を作成"
mkdir -p "$DROPIN_DIR"
cat > "$DROPIN_DIR/override.conf" <<EOF
[Service]
WorkingDirectory=$REPO_DIR
# 起動時は検知(detector)を止めておく。検知は「HIME-H-REAP に接続」で開始する設計。
# （up -d で一旦起動するが直後に stop。manual stop なので restart policy では復帰しない）
ExecStartPost=-/usr/bin/docker stop presence-detector
EOF
echo "    $DROPIN_DIR/override.conf"

echo "==> [2/4] systemctl daemon-reload"
systemctl daemon-reload

echo "==> [3/4] 起動時に自動で立ち上がるよう enable"
systemctl enable "$UNIT"

echo "==> [4/4] 今すぐ systemd 管理下に置く"
echo "    （docker compose up -d は冪等：起動中で設定一致なら何も再作成しません）"
systemctl start "$UNIT"

echo
echo "==================== 結果 ===================="
echo -n "  is-enabled : "; systemctl is-enabled "$UNIT" || true
echo -n "  is-active  : "; systemctl is-active  "$UNIT" || true
echo    "  WorkDir    : $(systemctl show -p WorkingDirectory --value "$UNIT")"
echo    "  コンテナ:"
docker ps --format '    {{.Names}}  {{.Status}}'
echo "=============================================="
echo
echo "✅ 完了。これで『docker compose down』しても、次の再起動で systemd が up し直します。"
