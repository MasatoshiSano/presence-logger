#!/usr/bin/env bash
# mode-prod.sh — 実運用モード：wlan0=HIME-H-REAP(工場/Oracle) + wlan1=AP(presence-hub)
#   インターネット(UFI)は切る → このPiはネット無し（Claude/SSH over UFI は切断される）。
#   工場記録(detector→bridge→Oracle)と、子Pi用の独自WiFi(AP)を同時に動かす。
# 実行: sudo bash desktop/presence-tools/mode-prod.sh
set -uo pipefail
if [[ $EUID -ne 0 ]]; then echo "root で実行: sudo bash $0"; exit 1; fi
UFI_CONN="${UFI_CONN:-UFI_103134}"; AP_CONN="${AP_CONN:-presence-hub-ap}"
HIME_CONN="${HIME_CONN:-HIME-H-REAP}"
say(){ printf '\n=== %s ===\n' "$*"; }

say "0) ドングル wlan1 を AP(presence-hub) に（先にAPを確保）"
nmcli --wait 20 connection up "$AP_CONN" >/dev/null 2>&1 || { echo "FAIL: AP 起動失敗"; exit 1; }

say "1) インターネット(UFI)を切り、工場 HIME-H-REAP を wlan0 に"
echo "  ※ ここで UFI 経由のネット/Claude/SSH は切れます"
nmcli connection modify "$UFI_CONN" connection.autoconnect no 2>/dev/null || true
iw reg set JP 2>/dev/null || true
nmcli connection down "$UFI_CONN" >/dev/null 2>&1 || true
# 隠しSSID＋5GHzは association が遅い → up を投げてから実状態を最大45秒ポーリング
nmcli --wait 45 connection up "$HIME_CONN" ifname wlan0 >/dev/null 2>&1 || true
ok="no"
for _i in $(seq 1 45); do
    nmcli -t -f NAME connection show --active 2>/dev/null | grep -Fxq "$HIME_CONN" && { ok="yes"; break; }
    sleep 1
done

say "確認"
echo "wlan0/1: "; nmcli -t -f DEVICE,STATE,CONNECTION device | grep -E "^wlan[01]:"
echo "AP種別: "; iw dev wlan1 info 2>/dev/null | grep -E "type|ssid|channel"
echo -n "工場接続(HIME): "; [[ "$ok" == yes ]] && echo "OK" || echo "NG(圏外/再試行を)"
echo -n "Oracle 10.166.5.93:1521: "; timeout 4 bash -c 'cat </dev/null >/dev/tcp/10.166.5.93/1521' 2>/dev/null && echo OPEN || echo NG
echo "DHCPリース(子Pi): "; cat /var/lib/NetworkManager/dnsmasq-wlan1.leases 2>/dev/null || echo "  (まだリースなし)"
echo
echo "→ 実運用モード。ネットは無し。開発に戻すには有線/別経路から:"
echo "    sudo bash desktop/presence-tools/mode-dev.sh"