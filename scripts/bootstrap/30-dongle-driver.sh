#!/usr/bin/env bash
# 30-dongle-driver.sh — ELECOM WDC-433DU2H2-B (RTL8811AU / 056e:4010) を使えるようにする。
#
# 素の Raspberry Pi OS では pegasus(USB有線LANドライバ)が 056e:4010 に誤マッチ
# して probe が -110 で失敗し、wlan1 自体が現れない。カーネル内蔵 rtw88 は
# 8821cu 系のみで RTL8811AU 非対応のため、外部ドライバを DKMS で入れる。
#
# USB キットにソースがあれば git clone しない(工場網へ切り替えたあとでも
# 組める)。カーネルが親と違っても、ソースから DKMS で組み直すので問題ない。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$HERE/../.." && pwd)"
# shellcheck source=scripts/lib/site-env.sh
source "$REPO_DIR/scripts/lib/site-env.sh"
# shellcheck source=scripts/lib/kit-copy.sh
source "$REPO_DIR/scripts/lib/kit-copy.sh"
PATCH="$HERE/patches/8821au-add-elecom-056e-4010.patch"
SRC_DIR="${DONGLE_SRC_DIR:-/usr/local/src/8821au}"
DRIVER_REPO="${DONGLE_REPO:-https://github.com/morrownr/8821au-20210708.git}"
DONGLE_KIT_SRC="${DONGLE_KIT_SRC:-$REPO_DIR/.kit/driver/8821au}"

dongle_render_modprobe_conf() {
    # rtw_country_code=JP … 未設定だと5GHzで CTRL-EVENT-ASSOC-REJECT status_code=1
    # rtw_power_mgnt=0    … 省電力ONだと 4-way handshake を落として no-secrets 切断
    printf 'options 8821au rtw_led_ctrl=1 rtw_country_code=JP rtw_power_mgnt=0\n'
}

# パッチが当たらなかったときの最後の手段。upstream が USB ID の並びを
# 変えるとパッチは文脈不一致で落ちるが、ID が1行入ればドライバは動く。
dongle_ensure_elecom_id() {
    local f="$1"
    [ -f "$f" ] || return 1
    grep -q '0x056E, 0x4010' "$f" && return 0
    local tmp
    tmp="$(mktemp)"
    awk '
        /USB_DEVICE\(0x/ && !done {
            print "\t{USB_DEVICE(0x056E, 0x4010), .driver_info = RTL8821}, /* ELECOM WDC-433DU2H2-B */"
            done=1
        }
        { print }
    ' "$f" > "$tmp" && mv "$tmp" "$f"
    grep -q '0x056E, 0x4010' "$f"
}

dongle_already_working() {
    local ifname="${1:-wlan1}" phy
    ip -br link show "$ifname" >/dev/null 2>&1 || return 1
    phy="${IW_PHY_NAME:-$(cat "/sys/class/net/$ifname/phy80211/name" 2>/dev/null)}"
    [ -n "$phy" ] || return 1
    iw phy "$phy" info 2>/dev/null | grep -q -- '\* AP'
}

# 「wlan1 が出た」だけでは足りない。rtw_country_code が JP でないと
# 5GHz で ASSOC-REJECT になり、症状が AP 設定ミスに見えてしまう。
dongle_verify() {
    local ifname="${1:-wlan1}" ok=0
    grep -q JP /sys/module/8821au/parameters/rtw_country_code 2>/dev/null \
        || { echo "rtw_country_code が JP ではありません" >&2; ok=1; }
    dongle_already_working "$ifname" \
        || { echo "$ifname が無い、または AP 非対応です" >&2; ok=1; }
    return "$ok"
}

dongle_ensure_source() {
    local dest="${1:-$SRC_DIR}"
    mkdir -p "$(dirname "$dest")"
    if [ -f "$dest/os_dep/linux/usb_intf.c" ] \
        && grep -q '0x056E, 0x4010' "$dest/os_dep/linux/usb_intf.c"; then
        return 0
    fi
    if [ -n "${DONGLE_KIT_SRC:-}" ] && [ -d "$DONGLE_KIT_SRC" ]; then
        echo "==> キットのドライバソースを使います"
        kit_copy_dir "$DONGLE_KIT_SRC" "$dest" || return 1
        dongle_ensure_elecom_id "$dest/os_dep/linux/usb_intf.c"
        return
    fi
    if [ ! -d "$dest" ]; then
        echo "==> ドライバソースを git clone"
        git clone --depth=1 "$DRIVER_REPO" "$dest" || return 1
    fi
    echo "==> ELECOM の USB ID を追加"
    if [ -f "$PATCH" ]; then
        git -C "$dest" apply "$PATCH" 2>/dev/null \
            || patch -p1 -d "$dest" < "$PATCH" 2>/dev/null \
            || true
    fi
    dongle_ensure_elecom_id "$dest/os_dep/linux/usb_intf.c"
}

main() {
    site_env_require
    local ifname="${AP_IF:-wlan1}"
    if dongle_already_working "$ifname"; then
        echo "$ifname は既に動作し AP 対応です。スキップします"
        return 0
    fi

    echo "==> 検出されている USB デバイス"; lsusb | grep -i "056e:4010" || true

    dongle_ensure_source "$SRC_DIR" || return 1

    # install-driver.sh がモジュールを読み込むので、オプションは先に置く。
    echo "==> モジュールオプションを配置"
    dongle_render_modprobe_conf > /etc/modprobe.d/8821au.conf

    echo "==> DKMS でビルド・導入"
    ( cd "$SRC_DIR" && ./install-driver.sh NoPrompt ) || return 1

    # 既に古いモジュールが載っていた場合、オプションは読み直さないと効かない。
    modprobe -r 8821au 2>/dev/null || true
    modprobe 8821au || true
    sleep 2

    dongle_verify "$ifname" || return 1
    echo "✅ $ifname が AP 対応で使えます"
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
