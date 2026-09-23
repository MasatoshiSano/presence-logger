"""フェーズ60(コンテナ起動と常駐化)を検証する。

detector を profiles: に入れる理由は2つ。カメラ無しのハブで誤って起動しない
ことと、clone 直後に `up -d --build` が detector のビルドで失敗しないこと
(Dockerfile が gitignore された .tflite を COPY するため)。
"""
import os
import subprocess
import textwrap

import yaml

from scripts.tests.shellhelp import REPO_ROOT, run_bash

SOURCE = "source scripts/bootstrap/60-stack.sh"

SITE = textwrap.dedent("""\
    HUB_MODE=1
    AP_GW_IP=10.42.0.1
    """)


def test_hub_mode_excludes_detector(tmp_path):
    f = tmp_path / "site.env"
    f.write_text(SITE, encoding="utf-8")
    out = run_bash(f'{SOURCE}; site_env_load "{f}"; stack_services',
                   env=dict(os.environ)).stdout.split()
    assert "detector" not in out
    assert set(out) == {"mosquitto", "oracle-jdbc", "bridge"}


def test_camera_mode_includes_detector(tmp_path):
    f = tmp_path / "site.env"
    f.write_text("HUB_MODE=0\nAP_GW_IP=10.42.0.1\n", encoding="utf-8")
    out = run_bash(f'{SOURCE}; site_env_load "{f}"; stack_services',
                   env=dict(os.environ)).stdout.split()
    assert "detector" in out


def test_env_file_carries_the_ap_gateway(tmp_path):
    f = tmp_path / "site.env"
    f.write_text(SITE, encoding="utf-8")
    dst = tmp_path / ".env"
    run_bash(f'{SOURCE}; site_env_load "{f}"; stack_write_env "{dst}"',
             env=dict(os.environ))
    assert "AP_GW_IP=10.42.0.1" in dst.read_text(encoding="utf-8")


def test_detector_is_behind_a_compose_profile():
    doc = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert doc["services"]["detector"].get("profiles") == ["camera"]


def test_default_compose_config_has_no_detector():
    # profiles 指定なしの `docker compose config` に detector が現れないこと
    out = subprocess.run(  # noqa: S603
        ["docker", "compose", "config", "--services"],  # noqa: S607
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        import pytest
        pytest.skip("docker compose が使えない環境")
    assert "detector" not in out.stdout.split()


def test_override_binds_the_configurable_gateway():
    body = (REPO_ROOT / "docker-compose.override.yml").read_text(encoding="utf-8")
    assert "${AP_GW_IP:-10.42.0.1}:1883:1883" in body


def test_autostart_dropin_omits_detector_stop_in_hub_mode():
    out = run_bash(
        'source desktop/presence-tools/setup-autostart.sh; HUB_MODE=1 autostart_dropin_body',
        env=dict(os.environ),
    ).stdout
    assert "presence-detector" not in out
    assert "WorkingDirectory=" in out


def test_autostart_dropin_stops_detector_when_camera_present():
    out = run_bash(
        'source desktop/presence-tools/setup-autostart.sh; HUB_MODE=0 autostart_dropin_body',
        env=dict(os.environ),
    ).stdout
    assert "ExecStartPost=-/usr/bin/docker stop presence-detector" in out


def test_kit_images_are_loaded_and_build_is_skipped(tmp_path, fake_bin):
    """キットの tar があるなら docker load して --no-build で上げる。

    --build を打つと detector の Dockerfile が .gitignore 済みの .tflite を
    COPY しようとして失敗する。カメラ無しのハブでは常に除外する。
    """
    fake_bin("docker", 'echo docker "$@" >> "$FAKE_LOG"; exit 0')
    kit = tmp_path / "docker-images"
    kit.mkdir()
    (kit / "presence-logger-bridge.tar").write_text("x", encoding="utf-8")
    proc = run_bash(
        f'{SOURCE}; stack_load_kit_images "{kit}" && stack_compose_up_args "{kit}"',
        env=dict(os.environ),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "docker load" in (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "--no-build" in proc.stdout
    assert "detector" not in proc.stdout


def test_build_is_used_when_there_is_no_kit(tmp_path):
    out = run_bash(
        f'{SOURCE}; stack_compose_up_args "{tmp_path}/none"',
        env=dict(os.environ),
    ).stdout
    assert "--build" in out
    assert "--no-build" not in out


def test_env_pins_the_compose_project_name(tmp_path):
    """ディレクトリ名が変わってもイメージ名がズレないようにする。

    キットの tar は presence-logger-bridge:latest として load される。
    プロジェクト名が変わると compose は別名のイメージを探して再ビルドに落ちる。
    """
    dst = tmp_path / ".env"
    run_bash(
        f'{SOURCE}; AP_GW_IP=10.42.0.1 stack_write_env "{dst}"',
        env=dict(os.environ),
    )
    assert "COMPOSE_PROJECT_NAME=presence-logger" in dst.read_text(encoding="utf-8")
