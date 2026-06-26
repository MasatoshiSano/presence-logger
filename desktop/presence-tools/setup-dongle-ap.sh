#!/usr/bin/env bash
# setup-dongle-ap.sh — ドングル wlan1 を AP(親機)にして、複数の子ラズパイを 1:多 で接続。
#   - 内蔵 wlan0（工場/Oracle）はそのまま
#   - wlan1 は AP 専用。10.42.0.1/24 + dnsmasq(DHCP) を NetworkManager(shared) が用意
#   - NAT/インターネット共有はしない（子Pi <-> このPi のローカル通信用）
# 実行: sudo bash desktop/presence-tools/setup-dongle-ap.sh
set -uo pipefail
if [[ $EUID -ne 0 ]]; then echo "root で実行: sudo bash $0"; exit 1; fi

AP_IF="${AP_IF:-wlan1}"
AP_CONN="${AP_CONN:-presence-hub-ap}"
AP_SSID="${AP_SSID:-presence-hub}"
AP_BAND="${AP_BAND:-bg}"                   # bg=2.4GHz（APはこちらが確実）
AP_CHANNEL="${AP_CHANNEL:-6}"
UFI_CONN="${UFI_CONN:-UFI_103134}"
# PSK はファイルに書かない。実行時に secrets.env(0600 root) から読む。
# 上書きしたい場合のみ環境変数 AP_PSK を渡す。
SECRETS_ENV="${SECRETS_ENV:-/etc/presence-logger/secrets.env}"
AP_PSK="${AP_PSK:-}"
say(){ printf '\n=== %s ===\n' "$*"; }

if [[ -z "$AP_PSK" && -f "$SECRETS_ENV" ]]; then
    AP_PSK="$(grep -E '^WIFI_AP_PSK=' "$SECRETS_ENV" | head -1 | cut -d= -f2-)"
fi
if [[ ${#AP_PSK} -lt 8 ]]; then
    echo "FAIL: WIFI_AP_PSK (8文字以上) が $SECRETS_ENV にありません"; exit 1
fi

say "0) ドングル($AP_IF)存在確認＆ドライバがAP対応か"
ip -br link show "$AP_IF" >/dev/null 2>&1 || { echo "FAIL: $AP_IF が無い"; exit 1; }
PHY=$(cat /sys/class/net/$AP_IF/phy80211/name)
iw phy "$PHY" info | grep -q -- "* AP" || { echo "FAIL: $PHY は AP 非対応"; exit 1; }
echo "  $AP_IF ($PHY) は AP 対応 OK"

say "1) インターネット($UFI_CONN)が wlan1 を使わないよう自動接続オフ＆切断"
nmcli connection modify "$UFI_CONN" connection.autoconnect no 2>/dev/null || true
nmcli device disconnect "$AP_IF" 2>/dev/null || true

say "2) AP プロファイル作成（2.4GHz ch$AP_CHANNEL / WPA2 / shared=DHCP付き）"
nmcli connection delete "$AP_CONN" >/dev/null 2>&1 || true
nmcli connection add type wifi ifname "$AP_IF" con-name "$AP_CONN" \
    ssid "$AP_SSID" \
    802-11-wireless.mode ap \
    802-11-wireless.band "$AP_BAND" \
    802-11-wireless.channel "$AP_CHANNEL" \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.proto rsn \
    wifi-sec.pairwise ccmp \
    wifi-sec.group ccmp \
    wifi-sec.psk "$AP_PSK" \
    ipv4.method shared \
    ipv6.method disabled \
    connection.autoconnect yes >/dev/null \
    || { echo "FAIL: AP プロファイル作成"; exit 1; }

say "3) AP 起動"
nmcli --wait 20 connection up "$AP_CONN" || { echo "FAIL: AP 起動失敗"; exit 1; }
sleep 3

say "検証"
echo "- インターフェース種別 (type AP であること):"
iw dev "$AP_IF" info 2>/dev/null | grep -E "type|ssid|channel|txpower"
echo "- AP のIP (10.42.0.1 等):"; ip -4 addr show "$AP_IF" | grep inet
echo "- DHCP(dnsmasq) 稼働:"; pgrep -a dnsmasq | grep -q "$AP_IF" && echo "  OK (wlan1向けdnsmasq)" || pgrep -a dnsmasq | head -1
echo "- デバイス状態:"; nmcli -t -f DEVICE,STATE,CONNECTION device | grep -E "wlan0|wlan1"
echo "- 工場(wlan0)が無事か:"; nmcli -t -f DEVICE,STATE device | grep "wlan0:connected" && echo "  工場接続 維持OK" || echo "  ⚠ wlan0 を確認"

echo
echo "------------------------------------------------------------"
echo " ✅ AP 構築完了（のはず）。子ラズパイ側でこのSSIDに接続:"
echo "      SSID : $AP_SSID"
echo "      PASS : (secrets.env の WIFI_AP_PSK)"
echo "    子Piは 10.42.0.x が自動で振られ、このPiは 10.42.0.1。"
echo "    例) 子Piから:  ping 10.42.0.1   /  ssh pi@10.42.0.1"
echo " 解除して元のインターネット(ドングル子機)に戻すには:"
echo "      sudo nmcli connection down $AP_CONN"
echo "      sudo nmcli connection modify $UFI_CONN connection.autoconnect yes"
echo "      sudo nmcli connection up $UFI_CONN ifname $AP_IF"
echo "------------------------------------------------------------"
