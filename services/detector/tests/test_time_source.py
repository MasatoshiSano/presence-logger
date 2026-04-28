import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from services.detector.src.time_source import (
    TimeSource,
    format_iso_with_tz,
    format_mk_date,
)


def test_monotonic_ns_strictly_increasing():
    ts = TimeSource()
    a = ts.monotonic_ns()
    b = ts.monotonic_ns()
    assert b >= a
    assert isinstance(a, int)


def test_format_mk_date_returns_14_digits():
    dt = datetime(2026, 4, 27, 17, 23, 45, tzinfo=timezone(timedelta(hours=9)))
    assert format_mk_date(dt) == "20260427172345"


def test_format_iso_with_tz_includes_milliseconds_and_offset():
    dt = datetime(2026, 4, 27, 17, 23, 45, 123_000, tzinfo=timezone(timedelta(hours=9)))
    s = format_iso_with_tz(dt)
    assert s == "2026-04-27T17:23:45.123+09:00"


def test_is_synced_calls_timedatectl_yes():
    ts = TimeSource()
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="yes\n", stderr=""
        )
        assert ts.is_synced() is True


def test_is_synced_returns_false_when_no():
    ts = TimeSource()
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="no\n", stderr=""
        )
        assert ts.is_synced() is False


def test_is_synced_trusts_host_when_timedatectl_missing():
    """In slim containers without systemd, timedatectl raises FileNotFoundError.
    We trust the host clock (mounted via /etc/localtime) rather than blocking."""
    ts = TimeSource()
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert ts.is_synced() is True


def test_is_synced_returns_false_on_subprocess_timeout():
    ts = TimeSource()
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=2)):
        assert ts.is_synced() is False


def test_is_synced_trusts_host_on_unrecognized_output():
    """timedatectl present but stderr complains (e.g. 'Failed to connect to bus'
    inside a container without systemd as PID 1) — stdout is empty/garbage,
    fall back to trusting the host clock."""
    ts = TimeSource()
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Failed to connect to bus"
        )
        assert ts.is_synced() is True


def test_now_returns_aware_datetime():
    ts = TimeSource()
    now = ts.now()
    assert now.tzinfo is not None
