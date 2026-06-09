"""HTTP client for the oracle-jdbc sidecar container.

The sidecar exposes a single POST /merge endpoint that accepts
application/x-www-form-urlencoded parameters and returns
text/plain key=value lines. This mirrors the contract used by
services/oracle-jdbc/src/Main.java.

We avoid python-oracledb here entirely: the HIME-H-REAP target DB
(HHC001) authenticates ZHH001 with a 10G verifier which the Thin
driver rejects (DPY-3015), and the Thick driver cannot be loaded
on a 16KB-page Pi 5. JDBC via ojdbc11.jar is the only path that
works (see /home/pi/oracle-himereap-guide/index.html).
"""
from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from services.bridge.src.oracle_client import MergeResult

_log = logging.getLogger("bridge.oracle_jdbc")


def _build_jdbc_url(profile_oracle: dict[str, Any]) -> str:
    return (
        f"jdbc:oracle:thin:@{profile_oracle['host']}:"
        f"{profile_oracle['port']}/{profile_oracle['service_name']}"
    )


def _parse_kv_body(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in body.splitlines():
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value
    return out


def execute_merge_via_jdbc(
    profile_oracle: dict[str, Any],
    *,
    proxy_url: str,
    table_name: str,
    mk_date: str,
    sta_no1: str,
    sta_no2: str,
    sta_no3: str,
    t1_status: int,
    upcmpflg: int | None = None,
    connect_timeout_ms: int = 10000,
    read_timeout_ms: int = 30000,
    http_timeout_seconds: float = 35.0,
) -> MergeResult:
    """POST a merge request to the oracle-jdbc sidecar and return its result.

    Never raises: transport failures are surfaced as a MergeResult with
    ora_code=None and a populated error_message, matching the python-oracledb
    path so the existing sender retry/circuit-breaker logic still applies.

    `upcmpflg` is the optional per-profile UPCMPFLG value. None omits the
    upcmpflg_value form field; the sidecar then builds an INSERT that does
    not touch the UPCMPFLG column.
    """
    fields = {
        "url": _build_jdbc_url(profile_oracle),
        "user": profile_oracle["user"],
        "password": profile_oracle["password"],
        "table_name": table_name,
        "mk_date": mk_date,
        "sta_no1": sta_no1,
        "sta_no2": sta_no2,
        "sta_no3": sta_no3,
        "t1_status": str(t1_status),
        "connect_timeout_ms": str(connect_timeout_ms),
        "read_timeout_ms": str(read_timeout_ms),
    }
    if upcmpflg is not None:
        fields["upcmpflg_value"] = str(upcmpflg)
    form = urllib.parse.urlencode(fields).encode("utf-8")

    request = urllib.request.Request(  # noqa: S310
        f"{proxy_url.rstrip('/')}/merge",
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=http_timeout_seconds) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return MergeResult(
            rows_affected=0,
            ora_code=None,
            error_message=f"jdbc-proxy HTTP {exc.code}: {exc.reason}",
        )
    except (urllib.error.URLError, TimeoutError) as exc:
        return MergeResult(
            rows_affected=0,
            ora_code=None,
            error_message=f"jdbc-proxy unreachable: {exc}",
        )

    fields = _parse_kv_body(body)
    raw_code = fields.get("ora_code", "").strip()
    try:
        ora_code: int | None = int(raw_code) if raw_code else None
    except ValueError:
        ora_code = None
    try:
        rows = int(fields.get("rows_affected", "0").strip() or "0")
    except ValueError:
        rows = 0
    return MergeResult(
        rows_affected=rows,
        ora_code=ora_code,
        error_message=fields.get("error_message", ""),
    )
