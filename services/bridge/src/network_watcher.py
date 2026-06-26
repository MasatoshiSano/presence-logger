import logging
import shlex
import subprocess

_log = logging.getLogger("bridge.network")


def parse_active_ssids(stdout: str) -> list[str]:
    """Parse `nmcli -t -f ACTIVE,SSID dev wifi` output. Return ALL active SSIDs in order.

    With a dual-WiFi setup (internal + USB dongle) more than one SSID is active at
    once, so callers need the full list to pick the right one. nmcli terse mode
    escapes colons in SSIDs with backslashes (e.g. `my\\:wifi`); we unescape that.
    The first column is `yes`/`no` for ACTIVE state.
    """
    ssids: list[str] = []
    for raw_line in stdout.splitlines():
        # Split on the first un-escaped colon.
        parts = _split_first_unescaped_colon(raw_line)
        if not parts or len(parts) < 2:
            continue
        active, ssid = parts[0], parts[1]
        if active.strip().lower() == "yes":
            ssids.append(ssid.replace("\\:", ":"))
    return ssids


def parse_nmcli_output(stdout: str) -> str | None:
    """Return the first active SSID, or None. Thin wrapper over parse_active_ssids."""
    active = parse_active_ssids(stdout)
    return active[0] if active else None


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
    def __init__(self, *, command: str, preferred_ssids: set[str] | None = None):
        self._argv = shlex.split(command)
        # SSIDs we have a profile for. In a dual-WiFi setup several SSIDs are
        # active at once (factory net on the internal NIC, internet on the
        # dongle); we must report the factory one so events are not dropped as
        # "unknown SSID". Empty/None preserves single-WiFi behaviour.
        self._preferred_ssids = preferred_ssids or set()
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
        active = parse_active_ssids(r.stdout)
        ssid = self._select_ssid(active)
        self.cached_ssid = ssid
        return ssid

    def _select_ssid(self, active: list[str]) -> str | None:
        """Pick the SSID to report from the currently-active ones.

        Prefer an SSID we have a profile for (the factory net); otherwise fall
        back to the first active SSID so unknown-SSID handling is unchanged.
        """
        for ssid in active:
            if ssid in self._preferred_ssids:
                return ssid
        return active[0] if active else None
