#!/usr/bin/env bash
# 30-dongle-driver.sh — ELECOM WDC-433DU2H2-B (RTL8811AU / 056e:4010)。
#
# USB キットにソースがあれば git clone しない。カーネルが親と違う場合でも
# ソースから DKMS で組み直す。
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
    printf 'options 8821au rtw_led_ctrl=1 rtw_country_code=JP rtw_power_mgnt=0\n'
}

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

dongle_ensure_source() {
    local dest="${1:-$SRC_DIR}"
    mkdir -p "$(dirname "$dest")"
    if [ -f "$dest/os_dep/linux/usb_intf.c" ] && grep -q '0x056E, 0x4010' "$dest/os_dep/linux/usb_intf.c"; then
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
    dongle_render_modprobe_conf > /etc/modprobe.d/8821au.conf

    if [ -x "$SRC_DIR/install-driver.sh" ]; then
        echo "==> DKMS でドライバを導入"
        ( cd "$SRC_DIR" && ./install-driver.sh NoPrompt ) || return 1
    fi

    if ! dongle_already_working "$ifname"; then
        echo "⚠ $ifname がまだ見えません。ドングルを挿し直してからフェーズ30 を再実行してください" >&2
        return 1
    fi
    echo "✅ ドングルドライバの準備ができました"
}

[[ "${BASH_SOURCE[0]}" == "$0" ]] && main "$@"
