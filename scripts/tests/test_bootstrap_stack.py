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
