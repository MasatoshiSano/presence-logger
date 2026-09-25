"""ハブ(カメラ無し)で既存デスクトップツールが壊れないことを検証する。

3つとも detector コンテナの存在を前提にしている:
 - connect/disconnect は docker start/stop presence-detector を叩く
 - watch-records は docker logs presence-detector を購読する
 - show-recent-records は絞り込み既定値を親の device.yaml station から取る
   (ハブでは placeholder なので、既定のままだと常に0件になる)
"""
import os

from scripts.tests.shellhelp import REPO_ROOT, run_bash


def test_connect_does_not_touch_detector_in_hub_mode(fake_bin):
    fake_bin("docker", 'printf "docker %s\\n" "$*" >> "$FAKE_LOG"')
    run_bash(
        'source desktop/presence-tools/connect-hime-h-reap.sh; '
        'HUB_MODE=1 detector_start',
        env=dict(os.environ), check=False,
    )
    assert "presence-detector" not in fake_bin.log.read_text(encoding="utf-8")


def test_connect_starts_detector_when_camera_present(fake_bin):
    fake_bin("docker", 'printf "docker %s\\n" "$*" >> "$FAKE_LOG"')
    run_bash(
        'source desktop/presence-tools/connect-hime-h-reap.sh; '
        'HUB_MODE=0 detector_start',
        env=dict(os.environ), check=False,
    )
    assert "presence-detector" in fake_bin.log.read_text(encoding="utf-8")


def test_watch_records_streams_bridge_only_in_hub_mode():
    out = run_bash(
        'source desktop/presence-tools/watch-records.sh; '
        'HUB_MODE=1 watch_containers',
        env=dict(os.environ)).stdout.split()
    assert out == ["presence-bridge"]


def test_watch_records_streams_both_with_camera():
    out = run_bash(
        'source desktop/presence-tools/watch-records.sh; '
        'HUB_MODE=0 watch_containers',
        env=dict(os.environ)).stdout.split()
    assert "presence-detector" in out
    assert "presence-bridge" in out


def test_recent_records_defaults_to_all_stations_in_hub_mode():
    # ハブでは親の局番は placeholder。既定で絞ると常に0件になる
    out = run_bash(
        'source desktop/presence-tools/show-recent-records.sh; '
        'HUB_MODE=1 recent_default_sta_no sta_no1 996',
        env=dict(os.environ)).stdout.strip()
    assert out == "*"


def test_disconnect_does_not_touch_detector_in_hub_mode(fake_bin):
    fake_bin("docker", 'printf "docker %s\\n" "$*" >> "$FAKE_LOG"')
    run_bash(
        'source desktop/presence-tools/disconnect-hime-h-reap.sh; '
        'HUB_MODE=1 detector_stop',
        env=dict(os.environ), check=False,
    )
    assert "presence-detector" not in fake_bin.log.read_text(encoding="utf-8")


def test_disconnect_stops_detector_when_camera_present(fake_bin):
    fake_bin("docker", 'printf "docker %s\\n" "$*" >> "$FAKE_LOG"')
    run_bash(
        'source desktop/presence-tools/disconnect-hime-h-reap.sh; '
        'HUB_MODE=0 detector_stop',
        env=dict(os.environ), check=False,
    )
    assert "presence-detector" in fake_bin.log.read_text(encoding="utf-8")


def test_recent_records_defaults_to_parent_station_when_camera_present():
    out = run_bash(
        'source desktop/presence-tools/show-recent-records.sh; '
        'HUB_MODE=0 recent_default_sta_no sta_no1 996',
        env=dict(os.environ),
    ).stdout.strip()
    assert out == "996"


def test_watch_records_does_not_hardcode_detector_docker_logs():
    body = (REPO_ROOT / "desktop/presence-tools/watch-records.sh").read_text(encoding="utf-8")
    assert "watch_containers" in body
    for line in body.splitlines():
        if "docker logs" in line:
            assert "presence-detector" not in line


def test_connect_raises_this_hubs_own_ap(fake_bin):
    """AP の接続名は <AP_SSID>-ap(フェーズ50 が作る名前)。

    以前は presence-hub-ap 決め打ちで、2台目(例 raspberrypi5-2-hub-ap)では
    「AP を起動できません」と出た。AP 自体は autoconnect で動いているので、
    動いているものを「動かない」と言う誤表示になっていた。
    """
    fake_bin("nmcli", 'printf "nmcli %s\\n" "$*" >> "$FAKE_LOG"')
    r = run_bash(
        'source desktop/presence-tools/connect-hime-h-reap.sh; '
        'AP_SSID=raspberrypi5-2-hub hub_ap_up',
        env=dict(os.environ), check=False,
    )
    assert r.returncode == 0, r.stderr
    log = fake_bin.log.read_text(encoding="utf-8")
    assert "connection up raspberrypi5-2-hub-ap" in log
    assert "presence-hub-ap" not in log


def test_connect_ap_name_falls_back_for_hosts_without_site_env(fake_bin):
    fake_bin("nmcli", 'printf "nmcli %s\\n" "$*" >> "$FAKE_LOG"')
    env = dict(os.environ)
    env.pop("AP_SSID", None)
    run_bash('source desktop/presence-tools/connect-hime-h-reap.sh; hub_ap_up',
             env=env, check=False)
    assert "connection up presence-hub-ap" in fake_bin.log.read_text(encoding="utf-8")
