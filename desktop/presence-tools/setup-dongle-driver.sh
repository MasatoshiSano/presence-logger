#!/usr/bin/env bash
# setup-dongle-driver.sh — ELECOM WDC-433DU2H2-B (RTL8811AU, 056e:4010) を
# dual-WiFi 用 wlan1 として使えるようにする。DKMS導入＋必須オプション設定まで一括。
#
# 実行（pi で1回だけ）:  sudo bash desktop/presence-tools/setup-dongle-driver.sh
#
# 前提: ~/8821au に morrownr/8821au があり、usb_intf.c に 056e:4010 が追記済み
#       （Claude が追記済み）。試しコンパイルは成功確認済み。
set -uo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "root で実行してください:  sudo bash $0"; exit 1
fi

SRC="/home/pi/8821au"
say(){ printf '\n=== %s ===\n' "$*"; }

say "1) 前提パッケージ（dkms 等）"
# カーネルヘッダ(build シンボリックリンク)は既に存在することを試しビルドで確認済み。
# raspberrypi-kernel-headers は現行 OS(Debian 13)に無いパッケージなので入れない。
apt-get install -y dkms build-essential bc \
    || { echo "FAIL: apt-get install"; exit 1; }
# 念のためヘッダの build リンクを確認（無ければ DKMS が失敗する）
if [[ ! -e "/lib/modules/$(uname -r)/build" ]]; then
    echo "FAIL: /lib/modules/$(uname -r)/build が無い。linux-headers-$(uname -r) を入れてください"; exit 1
fi

say "2) DKMS でドライバ導入（NoPrompt）"
cd "$SRC" || { echo "FAIL: $SRC が無い"; exit 1; }
./install-driver.sh NoPrompt || { echo "FAIL: install-driver.sh"; exit 1; }

say "3) ドライバオプションを dual-WiFi 用に上書き（JP / 省電力OFF）"
cat > /etc/modprobe.d/8821au.conf <<'EOF'
options 8821au rtw_led_ctrl=1 rtw_country_code=JP rtw_power_mgnt=0
EOF
echo "  -> /etc/modprobe.d/8821au.conf"

say "4) NetworkManager 側でも wlan1 の省電力を無効化（保険）"
cat > /etc/NetworkManager/conf.d/wifi-powersave-wlan1.conf <<'EOF'
[connection-wlan1-nopowersave]
match-device=interface-name:wlan1
wifi.powersave=2
EOF
systemctl reload NetworkManager 2>/dev/null || true

say "5) モジュール再ロードして反映"
modprobe -r 8821au 2>/dev/null || true
modprobe 8821au || { echo "FAIL: modprobe 8821au"; exit 1; }
sleep 2

say "検証"
echo "- country_code (JP であること):"
cat /sys/module/8821au/parameters/rtw_country_code 2>/dev/null || echo "  (取得不可)"
echo "- power_mgnt (0 であること):"
cat /sys/module/8821au/parameters/rtw_power_mgnt 2>/dev/null || echo "  (取得不可)"
echo "- wlan インターフェース (wlan1 が出ること):"
ip -br link show | grep -i wlan || echo "  (wlan なし)"
echo "- dkms 状態:"
dkms status 2>/dev/null | grep -i 8821au || echo "  (dkms 8821au なし)"

echo
if ip -br link show wlan1 >/dev/null 2>&1; then
    echo "✅ wlan1 が出ました。ドライバ導入 完了。"
    echo "   次は Claude に戻って『wlan1 出た』と伝えてください（経路設定フェーズへ）。"
else
    echo "⚠ wlan1 がまだ出ていません。dmesg 末尾を確認してください:"
    echo "   dmesg | grep -iE '8821au|rtl88|wlan' | tail -20"
fi
