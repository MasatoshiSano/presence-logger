"""ドングルドライバフェーズとコンテナ起動フェーズのキット対応。"""
import os
import textwrap

from scripts.tests.shellhelp import run_bash

DONGLE = "source scripts/bootstrap/30-dongle-driver.sh"
STACK = "source scripts/bootstrap/60-stack.sh"


def _env(extra=None):
    env = dict(os.environ)
    if extra:
        env.update(extra)
    return env


def test_dongle_uses_kit_source_instead_of_git_clone(tmp_path, fake_bin):
    fake_bin("git", 'echo git "$@" >> "$FAKE_LOG"; exit 1')
    kit_src = tmp_path / "8821au"
    kit_src.mkdir()
    (kit_src / "os_dep" / "linux").mkdir(parents=True)
    usb = kit_src / "os_dep" / "linux" / "usb_intf.c"
    usb.write_text(
        "static const struct usb_device_id rtw_usb_id_tbl[] = {\n"
        "\t{USB_DEVICE(0x0BDA, 0x0823), .driver_info = RTL8821},\n"
        "};\n",
        encoding="utf-8",
    )
    dest = tmp_path / "src" / "8821au"
    run_bash(
        f'{DONGLE}; DONGLE_KIT_SRC="{kit_src}" dongle_ensure_source "{dest}"',
        env=_env(),
    )
    log = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "clone" not in log
    copied = (dest / "os_dep" / "linux" / "usb_intf.c").read_text(encoding="utf-8")
    assert "0x056E, 0x4010" in copied


def test_dongle_skips_git_when_id_already_present(tmp_path, fake_bin):
    fake_bin("git", 'echo git "$@" >> "$FAKE_LOG"; exit 1')
    dest = tmp_path / "8821au"
    dest.mkdir()
    (dest / "os_dep" / "linux").mkdir(parents=True)
    usb = dest / "os_dep" / "linux" / "usb_intf.c"
    usb.write_text("{USB_DEVICE(0x056E, 0x4010), .driver_info = RTL8821},\n", encoding="utf-8")
    run_bash(
        f'{DONGLE}; dongle_ensure_source "{dest}"',
        env=_env({"DONGLE_KIT_SRC": ""}),
    )
    log = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "clone" not in log


def test_dongle_modprobe_conf_sets_jp_and_disables_power_mgmt():
    out = run_bash(f"{DONGLE}; dongle_render_modprobe_conf", env=_env()).stdout
    assert "rtw_country_code=JP" in out
    assert "rtw_power_mgnt=0" in out


def test_stack_does_not_enable_fleet_ui():
    from pathlib import Path
    text = Path("scripts/bootstrap/60-stack.sh").read_text(encoding="utf-8")
    assert "fleet-ui.service" not in text
    assert "systemctl enable --now fleet-ui" not in text
    assert "子をこのハブへ付ける" in text


def test_stack_services_omit_detector_in_hub_mode():
    out = run_bash(f'{STACK}; HUB_MODE=1 stack_services', env=_env()).stdout.split()
    assert out == ["mosquitto", "oracle-jdbc", "bridge"]


def test_stack_compose_args_skip_build_when_kit_images_exist(tmp_path):
    kit = tmp_path / ".kit" / "docker-images"
    kit.mkdir(parents=True)
    (kit / "bridge.tar").write_bytes(b"fake")
    args = run_bash(
        f'{STACK}; stack_compose_up_args "{kit}"',
        env=_env(),
    ).stdout.split()
    assert "--no-build" in args
    assert "--build" not in args
    assert "up" in args
    assert "-d" in args
    assert "detector" not in args


def test_stack_compose_args_build_when_no_kit_images(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    args = run_bash(
        f'{STACK}; stack_compose_up_args "{empty}"',
        env=_env(),
    ).stdout.split()
    assert "--build" in args
    assert "--no-build" not in args


def test_stack_load_calls_docker_load_for_each_tar(tmp_path, fake_bin):
    fake_bin("docker", 'echo docker "$@" >> "$FAKE_LOG"')
    kit = tmp_path / "images"
    kit.mkdir()
    (kit / "a.tar").write_bytes(b"a")
    (kit / "b.tar").write_bytes(b"b")
    run_bash(f'{STACK}; stack_load_kit_images "{kit}"', env=_env())
    log = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "load -i" in log
    assert "a.tar" in log
    assert "b.tar" in log


def test_docker_compose_helper_prefers_plugin_and_explains_when_missing(tmp_path, fake_bin):
    fake_bin(
        "docker",
        textwrap.dedent("""\
            if [ "$1" = compose ]; then
                echo "Docker Compose version v2.fake"
                exit 0
            fi
            echo docker "$@" >> "$FAKE_LOG"
            """),
    )
    out = run_bash(
        "source scripts/lib/docker-run.sh; docker_compose version",
        env=_env(),
    ).stdout
    assert "Compose" in out


def test_docker_compose_helper_errors_in_japanese_when_plugin_missing(tmp_path, fake_bin):
    fake_bin("docker", "exit 1")
    proc = run_bash(
        "source scripts/lib/docker-run.sh; docker_compose version",
        env=_env(),
        check=False,
    )
    assert proc.returncode != 0
    assert "docker compose" in proc.stderr.lower() or "プラグイン" in proc.stderr
