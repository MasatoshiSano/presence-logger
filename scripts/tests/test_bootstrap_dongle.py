"""フェーズ30(ドングルドライバ)を検証する。

rtw_country_code=JP が無いと5GHzでAPに拒否され、rtw_power_mgnt=0 が無いと
4-way handshake を取りこぼして切断する。どちらも省略できない。
"""
import os

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/bootstrap/30-dongle-driver.sh"


def test_modprobe_conf_has_both_mandatory_options():
    out = run_bash(f'{SOURCE}; dongle_render_modprobe_conf', env=dict(os.environ)).stdout
    assert "rtw_country_code=JP" in out
    assert "rtw_power_mgnt=0" in out


def test_already_working_when_interface_supports_ap(tmp_path, fake_bin):
    fake_bin("ip", 'exit 0')
    fake_bin("iw", 'echo "	Supported interface modes:"; echo "		 * AP"')
    proc = run_bash(f'{SOURCE}; IW_PHY_NAME=phy1 dongle_already_working wlan1',
                    env=dict(os.environ), check=False)
    assert proc.returncode == 0


def test_not_working_when_interface_missing(fake_bin):
    fake_bin("ip", 'exit 1')
    proc = run_bash(f'{SOURCE}; dongle_already_working wlan1',
                    env=dict(os.environ), check=False)
    assert proc.returncode != 0


def test_not_working_when_ap_mode_unsupported(fake_bin):
    fake_bin("ip", 'exit 0')
    fake_bin("iw", 'echo "	Supported interface modes:"; echo "		 * managed"')
    proc = run_bash(f'{SOURCE}; IW_PHY_NAME=phy1 dongle_already_working wlan1',
                    env=dict(os.environ), check=False)
    assert proc.returncode != 0


def test_patch_file_adds_the_elecom_usb_id():
    body = open("scripts/bootstrap/patches/8821au-add-elecom-056e-4010.patch",
                encoding="utf-8").read()
    assert "0x056E, 0x4010" in body
    assert "RTL8821" in body


def test_existing_src_dir_without_usb_id_still_gets_patched(tmp_path):
    # ディレクトリがあるだけでは適用済みではない。失敗後の残骸でも USB ID を足す。
    src = tmp_path / "8821au"
    usb = src / "os_dep" / "linux" / "usb_intf.c"
    usb.parent.mkdir(parents=True)
    context = [
        "\t{USB_DEVICE(0x056E, 0x4007), .driver_info = RTL8821}, /* ELECOM */",
        "\t{USB_DEVICE(0x056E, 0x400E), .driver_info = RTL8821}, /* ELECOM */",
        "\t{USB_DEVICE(0x056E, 0x400F), .driver_info = RTL8821}, /* ELECOM */",
        "\t{USB_DEVICE(0x0846, 0x9052), .driver_info = RTL8821}, /* Netgear */",
        "\t{USB_DEVICE(0x0E66, 0x0023), .driver_info = RTL8821}, /* HAWKING */",
        "\t{USB_DEVICE(0x2001, 0x3314), .driver_info = RTL8821}, /* D-Link */",
    ]
    # パッチ hunk は 205 行目から。git apply がオフセット拒否しないよう揃える。
    body = "\n".join([f"/* {i} */" for i in range(1, 205)] + context) + "\n"
    usb.write_text(body, encoding="utf-8")
    assert "0x4010" not in body
    env = dict(os.environ)
    env["DONGLE_SRC_DIR"] = str(src)
    run_bash(f"{SOURCE}; dongle_ensure_source", env=env)
    assert "0x056E, 0x4010" in usb.read_text(encoding="utf-8")
