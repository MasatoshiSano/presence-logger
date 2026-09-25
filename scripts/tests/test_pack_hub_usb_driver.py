"""pack-hub-usb.sh がドライバソースを見失わないことの検証。

手順書どおり `sudo bash scripts/pack-hub-usb.sh …` と打つと、Debian の sudo は
HOME を /root に差し替える。ドライバソースは /home/pi/8821au にしか無いので
${HOME}/8821au では見つからず、しかも見つからなくても何も言わずに
「✅ USB キットを書きました」まで進んでいた。そのキットで作る新機は、
フェーズ30 で GitHub から clone できる回線が無いとドングルが使えない。
"""
import os

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/pack-hub-usb.sh"


def _driver_tree(home):
    src = home / "8821au"
    src.mkdir(parents=True)
    (src / "Makefile").write_text("obj-m := 8821au.o\n", encoding="utf-8")
    return src


def _sudo_env(tmp_path, fake_bin, extra=None):
    """sudo 直後を装う: HOME は root 側、SUDO_USER は pi。"""
    pi_home = tmp_path / "home" / "pi"
    pi_home.mkdir(parents=True)
    root_home = tmp_path / "root"
    root_home.mkdir()
    fake_bin(
        "getent",
        f'[ "$1" = passwd ] && [ "$2" = pi ] && '
        f'echo "pi:x:1000:1000::{pi_home}:/bin/bash"',
    )
    env = dict(os.environ)
    env.pop("PACK_DRIVER_SRC", None)
    env.pop("PACK_ALLOW_NO_DRIVER", None)
    env.update({
        "HOME": str(root_home), "SUDO_USER": "pi",
        # フェーズ30 を通った機械には /usr/local/src/8821au がある。
        # 実行機の状態で結果を変えないよう、既定では無い場所を指す。
        "PACK_SYSTEM_DRIVER_DIR": str(tmp_path / "no-system-driver"),
    })
    env.update(extra or {})
    return env, pi_home


def test_driver_is_found_in_the_invoking_users_home_under_sudo(tmp_path, fake_bin):
    env, pi_home = _sudo_env(tmp_path, fake_bin)
    src = _driver_tree(pi_home)
    out = run_bash(f"{SOURCE}; pack_find_driver_src", env=env, check=False).stdout.strip()
    assert out == str(src)


def test_explicit_driver_src_wins(tmp_path, fake_bin):
    env, pi_home = _sudo_env(tmp_path, fake_bin)
    _driver_tree(pi_home)
    other = _driver_tree(tmp_path / "elsewhere")
    env["PACK_DRIVER_SRC"] = str(other)
    out = run_bash(f"{SOURCE}; pack_find_driver_src", env=env, check=False).stdout.strip()
    assert out == str(other)


def _kit_inputs(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    site = tmp_path / "site.env"
    site.write_text(
        "HUB_HOSTNAME=parent\nFACTORY_IP=172.22.13.17/24\nAP_SSID=presence-hub\n"
        "PARENT_STA_NO1=1\nPARENT_STA_NO2=2\nPARENT_STA_NO3=3\n",
        encoding="utf-8",
    )
    secrets = tmp_path / "secrets.env"
    secrets.write_text("WIFI_PSK_HIMEREAP=x\n", encoding="utf-8")
    return src, {
        "PACK_SKIP_DOCKER": "1",
        "PACK_SITE_ENV": str(site),
        "PACK_SECRETS": str(secrets),
    }


def test_kit_without_driver_is_not_reported_as_done(tmp_path, fake_bin):
    src, extra = _kit_inputs(tmp_path)
    env, _ = _sudo_env(tmp_path, fake_bin, extra)          # どこにもドライバが無い
    dest = tmp_path / "usb"
    r = run_bash(f'{SOURCE}; pack_hub_kit "{src}" "{dest}"', env=env, check=False)
    assert r.returncode != 0
    assert "✅" not in r.stdout
    assert "PACK_DRIVER_SRC" in r.stderr                   # 次に何をすればよいかを示す


def test_kit_without_driver_can_be_made_on_purpose(tmp_path, fake_bin):
    src, extra = _kit_inputs(tmp_path)
    env, _ = _sudo_env(tmp_path, fake_bin, {**extra, "PACK_ALLOW_NO_DRIVER": "1"})
    dest = tmp_path / "usb"
    r = run_bash(f'{SOURCE}; pack_hub_kit "{src}" "{dest}"', env=env, check=False)
    assert r.returncode == 0, r.stderr
    assert "ドライバ" in r.stderr                            # 黙っては通さない


def test_kit_under_sudo_carries_the_driver(tmp_path, fake_bin):
    src, extra = _kit_inputs(tmp_path)
    env, pi_home = _sudo_env(tmp_path, fake_bin, extra)
    _driver_tree(pi_home)
    dest = tmp_path / "usb"
    r = run_bash(f'{SOURCE}; pack_hub_kit "{src}" "{dest}"', env=env, check=False)
    assert r.returncode == 0, r.stderr
    assert (dest / "presence-hub-kit" / ".kit" / "driver" / "8821au" / "Makefile").is_file()


def test_system_driver_dir_is_preferred_when_present(tmp_path, fake_bin):
    """フェーズ30 は /usr/local/src/8821au に置く。ハブから pack するときはそれを使う。"""
    env, pi_home = _sudo_env(tmp_path, fake_bin)
    _driver_tree(pi_home)
    system = _driver_tree(tmp_path / "usr-local-src")
    env["PACK_SYSTEM_DRIVER_DIR"] = str(system)
    out = run_bash(f"{SOURCE}; pack_find_driver_src", env=env, check=False).stdout.strip()
    assert out == str(system)
