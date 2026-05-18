#!/usr/bin/env bash
# taden-ot-ap 環境でオンプレ Oracle に接続できるか自己診断するスクリプト。
# Claude が使えない環境(オフライン)でも単独で実行できる。
#
# 使い方:
#   bash /home/pi/Apps/presence-logger/scripts/verify-onprem.sh
#
# 想定する事前状態:
#   - WiFi: taden-ot-ap に接続済み(IP: 172.29.1.4/24)
#   - presence-logger コンテナは起動していてもしていなくてもよい

set -u

# Source operator-managed secrets if present so passwords stay out of git.
if [ -r /etc/presence-logger/secrets.env ]; then
    # shellcheck disable=SC1091
    set -a; . /etc/presence-logger/secrets.env; set +a
fi

ORACLE_HOST="${ORACLE_HOST:-10.168.252.16}"
ORACLE_PORT="${ORACLE_PORT:-1521}"
ORACLE_SERVICE="${ORACLE_SERVICE:-HHS001}"
ORACLE_USER="${ORACLE_USER:-ZHH001}"
ORACLE_PASSWORD="${ORACLE_PASSWORD:-${ORACLE_PASSWORD_ONPREM:-}}"
GATEWAY="${GATEWAY:-172.29.1.254}"
DNS1="${DNS1:-192.168.250.1}"
EXPECTED_IP="${EXPECTED_IP:-172.29.1.4}"

if [ -z "$ORACLE_PASSWORD" ]; then
    echo "ERROR: ORACLE_PASSWORD が空です。" >&2
    echo "  /etc/presence-logger/secrets.env に ORACLE_PASSWORD_ONPREM=... を設定するか" >&2
    echo "  ORACLE_PASSWORD=... env を渡してください。" >&2
    exit 2
fi

pass() { printf "  [\033[32mPASS\033[0m] %s\n" "$1"; }
fail() { printf "  [\033[31mFAIL\033[0m] %s\n" "$1"; FAILED=1; }
warn() { printf "  [\033[33mWARN\033[0m] %s\n" "$1"; }

FAILED=0

echo "===================================================="
echo " presence-logger オンプレ接続自己診断"
echo "===================================================="

echo
echo "[1/7] 現在の WiFi と IP"
CUR_SSID=$(iwgetid -r 2>/dev/null || echo "")
CUR_IP=$(ip -4 -o addr show wlan0 2>/dev/null | awk '{print $4}' | cut -d/ -f1)
echo "  SSID: ${CUR_SSID:-<none>}"
echo "  IP  : ${CUR_IP:-<none>}"
if [ "$CUR_SSID" = "taden-ot-ap" ]; then pass "WiFi=taden-ot-ap"; else fail "WiFi が taden-ot-ap ではない"; fi
if [ "$CUR_IP" = "$EXPECTED_IP" ]; then pass "IP=$EXPECTED_IP"; else warn "IP=$CUR_IP (期待 $EXPECTED_IP)"; fi

echo
echo "[2/7] ゲートウェイ ($GATEWAY) 到達"
if ping -c 2 -W 2 "$GATEWAY" >/dev/null 2>&1; then pass "ping OK"; else fail "ping NG"; fi

echo
echo "[3/7] DNS ($DNS1) 到達"
if ping -c 2 -W 2 "$DNS1" >/dev/null 2>&1; then pass "ping OK"; else warn "ping NG (DNS不可でもOracle接続は host 指定なので可能)"; fi

echo
echo "[4/7] Oracle ホスト ($ORACLE_HOST) 到達"
if ping -c 2 -W 2 "$ORACLE_HOST" >/dev/null 2>&1; then pass "ping OK"; else warn "ping NG (ICMP遮断の可能性。TCPで再確認)"; fi

echo
echo "[5/7] TCP $ORACLE_HOST:$ORACLE_PORT 接続"
if timeout 5 bash -c "</dev/tcp/$ORACLE_HOST/$ORACLE_PORT" 2>/dev/null; then pass "TCP open"; else fail "TCP closed/timeout"; fi

echo
echo "[6/7] Oracle ログイン (ZHH001@HHS001)"
python3 - <<PY 2>&1 | sed 's/^/  /'
import sys
try:
    import oracledb
except ImportError:
    print("python-oracledb 未インストール"); sys.exit(2)
try:
    conn = oracledb.connect(user="$ORACLE_USER", password="$ORACLE_PASSWORD",
                            dsn=oracledb.makedsn("$ORACLE_HOST", $ORACLE_PORT, service_name="$ORACLE_SERVICE"))
    cur = conn.cursor()
    cur.execute("SELECT SYSDATE, USER FROM DUAL")
    sysdate, usr = cur.fetchone()
    print(f"[PASS] SYSDATE={sysdate}  USER={usr}")
    cur.execute("SELECT COUNT(*) FROM HF1RCM01")
    print(f"[PASS] HF1RCM01 row count = {cur.fetchone()[0]}")
    conn.close()
except Exception as e:
    print(f"[FAIL] {type(e).__name__}: {str(e)[:300]}")
    sys.exit(1)
PY
ORA_RC=$?
if [ "$ORA_RC" -ne 0 ]; then FAILED=1; fi

echo
echo "[7/7] presence-logger コンテナ稼働状況"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q presence-bridge; then
  pass "presence-bridge コンテナ稼働中"
  echo
  echo "  bridge の SSID 認識と inbox 状態:"
  docker logs --tail 3 presence-bridge 2>&1 | grep -F '"periodic"' | tail -1 | sed 's/^/    /'
else
  warn "presence-* コンテナ未起動(下記コマンドで起動可)"
  echo "    sudo docker compose -f /home/pi/Apps/presence-logger/docker-compose.yml up -d"
fi

echo
echo "===================================================="
if [ "$FAILED" = "0" ]; then
  printf " 結果: \033[32m✅ 全項目 PASS — DB 接続可能\033[0m\n"
else
  printf " 結果: \033[31m❌ 失敗あり — 上記の FAIL を確認してください\033[0m\n"
fi
echo "===================================================="
