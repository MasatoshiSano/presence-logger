import subprocess
import time
from datetime import datetime

SYNC_COMMAND = ["timedatectl", "show", "-p", "NTPSynchronized", "--value"]


def format_mk_date(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S")


def format_iso_with_tz(dt: datetime) -> str:
    # ISO 8601 with milliseconds + offset. Python's isoformat() with timespec=milliseconds
    # gives the desired output when dt is timezone-aware.
    return dt.isoformat(timespec="milliseconds")


class TimeSource:
    """Wraps monotonic and wall-clock access plus SNTP sync polling."""

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()

    def now(self) -> datetime:
        return datetime.now().astimezone()

    def is_synced(self) -> bool:
        try:
            r = subprocess.run(  # noqa: S603 (fixed argv, no shell)
                SYNC_COMMAND, capture_output=True, text=True, timeout=2.0, check=False
            )
        except FileNotFoundError:
            # `timedatectl` not installed — trust the host clock (typical in slim
            # containers; /etc/localtime is mounted from the host where
            # systemd-timesyncd keeps NTP in sync).
            return True
        except (subprocess.TimeoutExpired, OSError):
            return False
        out = r.stdout.strip().lower()
        if out == "yes":
            return True
        if out == "no":
            return False
        # Empty or unrecognized output (e.g. "Failed to connect to bus" inside a
        # container without systemd PID 1) — fall back to trusting the host.
        return True
