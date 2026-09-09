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
