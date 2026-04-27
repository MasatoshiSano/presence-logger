import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from services.bridge.src.time_watcher import TimeWatcher


def test_initial_state_unsynced():
    tw = TimeWatcher(
        command="timedatectl show -p NTPSynchronized --value",
        monotonic_clock=lambda: 0,
    )
    assert tw.is_synced is False
    assert tw.baseline is None


def test_acquire_baseline_when_sync_transitions_true():
    mono = [10_000_000_000, 13_000_000_000]
    tw = TimeWatcher(
        command="timedatectl show -p NTPSynchronized --value",
        monotonic_clock=lambda: mono.pop(0),
        wall_clock=lambda: datetime(
            2026, 4, 27, 17, 23, 51, tzinfo=timezone(timedelta(hours=9))
        ),
    )
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="no\n", stderr=""
        )
        tw.poll()
        assert tw.is_synced is False
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="yes\n", stderr=""
        )
        tw.poll()
    assert tw.is_synced is True
    assert tw.baseline is not None
    assert tw.baseline.sync_monotonic_ns == 13_000_000_000


def test_baseline_is_not_recaptured_while_already_synced():
    mono = [13_000_000_000, 20_000_000_000]
    wall_dts = [
        datetime(2026, 4, 27, 17, 23, 51, tzinfo=timezone(timedelta(hours=9))),
        datetime(2026, 4, 27, 18, 0, 0, tzinfo=timezone(timedelta(hours=9))),
    ]
    tw = TimeWatcher(
        command="t",
        monotonic_clock=lambda: mono.pop(0),
        wall_clock=lambda: wall_dts.pop(0),
    )
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="yes\n", stderr=""
        )
        tw.poll()
        first_baseline = tw.baseline
        tw.poll()
    assert tw.baseline is first_baseline


def test_sync_loss_resets_baseline():
    mono = [13_000_000_000, 14_000_000_000]
    tw = TimeWatcher(
        command="t",
        monotonic_clock=lambda: mono.pop(0),
        wall_clock=lambda: datetime(
            2026, 4, 27, 17, 23, 51, tzinfo=timezone(timedelta(hours=9))
        ),
    )
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="yes\n", stderr=""
        )
        tw.poll()
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="no\n", stderr=""
        )
        tw.poll()
    assert tw.is_synced is False
    assert tw.baseline is None
