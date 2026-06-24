import subprocess
from unittest.mock import patch

from services.bridge.src.network_watcher import (
    NetworkWatcher,
    parse_active_ssids,
    parse_nmcli_output,
)


def test_parse_nmcli_returns_active_ssid():
    out = "no:guest_wifi\nyes:factory_a_wifi\nno:other\n"
    assert parse_nmcli_output(out) == "factory_a_wifi"


def test_parse_nmcli_empty_returns_none():
    assert parse_nmcli_output("") is None


def test_parse_nmcli_no_active_returns_none():
    assert parse_nmcli_output("no:a\nno:b\n") is None


def test_parse_nmcli_handles_ssid_with_colon():
    # nmcli escapes embedded colons with backslash; mimic that.
    out = r"yes:my\:wifi"
    assert parse_nmcli_output(out) == "my:wifi"


def test_parse_active_ssids_returns_all_active():
    # Dual-WiFi: two interfaces up at once → both lines active.
    out = "yes:GallaxyS23FE\nno:guest\nyes:taden-ot-ap\n"
    assert parse_active_ssids(out) == ["GallaxyS23FE", "taden-ot-ap"]


def test_get_current_ssid_prefers_known_profile_in_dual_wifi():
    # Internet SSID is listed first, but the factory profile SSID must win so
    # events are routed to the profile instead of being dropped as unknown.
    nw = NetworkWatcher(
        command="nmcli -t -f ACTIVE,SSID dev wifi",
        preferred_ssids={"taden-ot-ap"},
    )
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="yes:GallaxyS23FE\nyes:taden-ot-ap\n", stderr=""
        )
        assert nw.get_current_ssid() == "taden-ot-ap"


def test_get_current_ssid_falls_back_to_first_when_no_preferred_match():
    nw = NetworkWatcher(command="nmcli ...", preferred_ssids={"taden-ot-ap"})
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="yes:GallaxyS23FE\n", stderr=""
        )
        assert nw.get_current_ssid() == "GallaxyS23FE"


def test_get_current_ssid_runs_command_and_parses():
    nw = NetworkWatcher(command="nmcli -t -f ACTIVE,SSID dev wifi")
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="yes:factory_a_wifi\n", stderr=""
        )
        assert nw.get_current_ssid() == "factory_a_wifi"


def test_get_current_ssid_returns_none_on_command_error():
    nw = NetworkWatcher(command="nmcli ...")
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert nw.get_current_ssid() is None


def test_cache_returns_last_value_until_refreshed():
    nw = NetworkWatcher(command="nmcli ...")
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="yes:factory_a_wifi\n", stderr=""
        )
        assert nw.get_current_ssid() == "factory_a_wifi"
    # Now nmcli is unavailable, but cache still serves last good value.
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert nw.cached_ssid == "factory_a_wifi"
