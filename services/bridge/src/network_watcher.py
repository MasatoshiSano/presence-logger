import logging
import shlex
import subprocess

_log = logging.getLogger("bridge.network")


def parse_nmcli_output(stdout: str) -> str | None:
    """Parse `nmcli -t -f ACTIVE,SSID dev wifi` output. Returns the active SSID or None.

    nmcli terse mode escapes colons in SSIDs with backslashes (e.g. `my\\:wifi`); we unescape
    that. The first column is `yes`/`no` for ACTIVE state.
    """
    for raw_line in stdout.splitlines():
        # Split on the first un-escaped colon.
        parts = _split_first_unescaped_colon(raw_line)
        if not parts or len(parts) < 2:
            continue
        active, ssid = parts[0], parts[1]
        if active.strip().lower() == "yes":
            return ssid.replace("\\:", ":")
    return None


def _split_first_unescaped_colon(line: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            buf.append(line[i:i + 2])
            i += 2
            continue
        if ch == ":":
            out.append("".join(buf))
            buf = []
            out.append(line[i + 1:])
            return out
        buf.append(ch)
        i += 1
    if buf:
        out.append("".join(buf))
    return out


class NetworkWatcher:
    def __init__(self, *, command: str):
        self._argv = shlex.split(command)
        self.cached_ssid: str | None = None

    def get_current_ssid(self) -> str | None:
        try:
            r = subprocess.run(  # noqa: S603
                self._argv,
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
            _log.warning(
                "nmcli_failed",
                extra={
                    "event": "nmcli_failed",
                    "error": {"type": type(e).__name__, "message": str(e)},
                },
            )
            return self.cached_ssid
        if r.returncode != 0:
            _log.warning(
                "nmcli_nonzero",
                extra={
                    "event": "nmcli_nonzero",
                    "rc": r.returncode,
                    "stderr": r.stderr.strip(),
                },
            )
            return self.cached_ssid
        ssid = parse_nmcli_output(r.stdout)
        self.cached_ssid = ssid
        return ssid
