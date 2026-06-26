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


# --- dual-WiFi: multiple active SSIDs at once (dongle + internal) ---

def test_parse_active_ssids_returns_all_in_order():
    out = "no:guest\nyes:UFI_103134\nyes:HIME-H-REAP\nno:other\n"
    assert parse_active_ssids(out) == ["UFI_103134", "HIME-H-REAP"]


def test_parse_active_ssids_empty_returns_empty_list():
    assert parse_active_ssids("") == []
    assert parse_active_ssids("no:a\nno:b\n") == []


def test_prefers_configured_ssid_when_multiple_active():
    # Dongle (internet) and factory net both up. nmcli lists internet first,
    # but we must pick the configured factory SSID so events are not dropped.
    nw = NetworkWatcher(
        command="nmcli ...", preferred_ssids={"HIME-H-REAP"}
    )
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="yes:UFI_103134\nyes:HIME-H-REAP\n", stderr="",
        )
        assert nw.get_current_ssid() == "HIME-H-REAP"


def test_falls_back_to_first_active_when_none_preferred():
    # No active SSID matches a configured profile -> keep first active
    # (so existing unknown_ssid_policy handling is unchanged).
    nw = NetworkWatcher(
        command="nmcli ...", preferred_ssids={"HIME-H-REAP"}
    )
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="yes:UFI_103134\nyes:home_wifi\n", stderr="",
        )
        assert nw.get_current_ssid() == "UFI_103134"


def test_backward_compat_no_preferred_returns_first_active():
    nw = NetworkWatcher(command="nmcli ...")
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="yes:UFI_103134\nyes:HIME-H-REAP\n", stderr="",
        )
        assert nw.get_current_ssid() == "UFI_103134"
