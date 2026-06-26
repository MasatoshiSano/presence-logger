#!/usr/bin/env bash
# mode-dev.sh — 開発モード：wlan0=UFI(インターネット/Claude) + wlan1=AP(presence-hub)
#   工場(HIME-H-REAP)には繋がない。ネットありで子Pi接続テストもできる。
# 実行: sudo bash desktop/presence-tools/mode-dev.sh
set -uo pipefail
if [[ $EUID -ne 0 ]]; then echo "root で実行: sudo bash $0"; exit 1; fi
UFI_CONN="${UFI_CONN:-UFI_103134}"; AP_CONN="${AP_CONN:-presence-hub-ap}"
HIME_CONN="${HIME_CONN:-HIME-H-REAP}"
say(){ printf '\n=== %s ===\n' "$*"; }

say "1) 工場(HIME)が wlan0 を握っていたら外し、UFI を wlan0 に"
nmcli connection modify "$HIME_CONN" connection.autoconnect no 2>/dev/null || true
nmcli connection modify "$UFI_CONN" connection.autoconnect yes connection.interface-name wlan0 2>/dev/null || true
nmcli --wait 20 connection up "$UFI_CONN" ifname wlan0 >/dev/null 2>&1 || true

say "2) ドングル wlan1 を AP(presence-hub) に"
nmcli --wait 20 connection up "$AP_CONN" >/dev/null 2>&1 || { echo "FAIL: AP 起動失敗"; exit 1; }
sleep 3

say "確認"
echo "wlan0/1: "; nmcli -t -f DEVICE,STATE,CONNECTION device | grep -E "^wlan[01]:"
echo -n "インターネット(Claude): "; ping -c1 -W2 8.8.8.8 >/dev/null 2>&1 && echo OK || echo NG
echo "AP種別: "; iw dev wlan1 info 2>/dev/null | grep -E "type|ssid|channel"
echo "AP IP : "; ip -4 addr show wlan1 | grep inet
echo "DHCPリース(子Pi): "; cat /var/lib/NetworkManager/dnsmasq-wlan1.leases 2>/dev/null || echo "  (まだリースなし=子Pi未接続)"
echo
echo "→ 開発モード。子Piは SSID 'presence-hub' に接続すれば 10.42.0.x を取得。"