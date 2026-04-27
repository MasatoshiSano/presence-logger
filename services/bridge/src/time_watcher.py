import logging
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

_log = logging.getLogger("bridge.time")


@dataclass(frozen=True)
class SyncBaseline:
    sync_wall: datetime
    sync_monotonic_ns: int


class TimeWatcher:
    def __init__(
        self,
        *,
        command: str,
        monotonic_clock: Callable[[], int] | None = None,
        wall_clock: Callable[[], datetime] | None = None,
    ):
        import time as _time
        self._argv = shlex.split(command)
        self._mono = monotonic_clock or _time.monotonic_ns
        self._wall = wall_clock or (lambda: datetime.now().astimezone())
        self.is_synced: bool = False
        self.baseline: SyncBaseline | None = None

    def poll(self) -> None:
        now_mono_ns = self._mono()
        synced_now = self._query()
        if synced_now and not self.is_synced:
            self.baseline = SyncBaseline(
                sync_wall=self._wall(),
                sync_monotonic_ns=now_mono_ns,
            )
            _log.info(
                "sync_acquired",
                extra={
                    "event": "sync_acquired",
                    "sync_wall_iso": self.baseline.sync_wall.isoformat(timespec="milliseconds"),
                    "sync_monotonic_ns": self.baseline.sync_monotonic_ns,
                },
            )
        elif not synced_now and self.is_synced:
            _log.warning("sync_lost", extra={"event": "sync_lost"})
            self.baseline = None
        self.is_synced = synced_now

    def _query(self) -> bool:
        try:
            r = subprocess.run(  # noqa: S603
                self._argv,
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False
        return r.stdout.strip().lower() == "yes"
