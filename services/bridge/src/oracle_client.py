import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import oracledb

_log = logging.getLogger("bridge.oracle")

ClientMode = Literal["thin", "thick"]

# Map python-oracledb (thin) "DPY-*" error codes to their ORA-* equivalents
# so a single permanent_ora_codes list in bridge config can drive the circuit
# breaker regardless of which client mode produced the failure.
_DPY_TO_ORA: dict[str, int] = {
    "DPY-6001": 12514,  # service not registered with listener (≈ ORA-12514)
}


@dataclass
class MergeResult:
    rows_affected: int
    ora_code: int | None
    error_message: str


def _extract_ora_code(e: oracledb.DatabaseError) -> tuple[int | None, str]:
    if not e.args or not hasattr(e.args[0], "code"):
        return None, str(e)
    err = e.args[0]
    raw_code = getattr(err, "code", 0) or 0
    full_code = getattr(err, "full_code", "") or ""
    message = str(getattr(err, "message", "") or str(e))
    try:
        ora_code: int | None = int(raw_code) if raw_code else None
    except (TypeError, ValueError):
        ora_code = None
    if ora_code is None and full_code in _DPY_TO_ORA:
        ora_code = _DPY_TO_ORA[full_code]
    if ora_code is None:
        cause = getattr(e, "__cause__", None)
        if isinstance(cause, oracledb.DatabaseError):
            nested, _ = _extract_ora_code(cause)
            if nested is not None:
                ora_code = nested
    return ora_code, message


def build_merge_statement(*, table_name: str, include_upcmpflg: bool = False) -> str:
    # table_name comes from validated config, not user input.
    # When include_upcmpflg=True the INSERT column list carries UPCMPFLG and a
    # 6th bind variable (:6) is emitted -- callers must pass the value.
    # When False, UPCMPFLG is omitted from the INSERT entirely (DB default /
    # NULL applies). Whether to include is per-profile via
    # profiles.yaml::oracle.upcmpflg.
    if include_upcmpflg:
        using_clause = (
            "USING (SELECT :1 AS MK_DATE, :2 AS STA_NO1, :3 AS STA_NO2, "
            ":4 AS STA_NO3, :5 AS T1_STATUS, :6 AS UPCMPFLG FROM dual) s"
        )
        insert_cols = "INSERT (MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS, UPCMPFLG)"
        insert_vals = "VALUES (s.MK_DATE, s.STA_NO1, s.STA_NO2, s.STA_NO3, s.T1_STATUS, s.UPCMPFLG)"
    else:
        using_clause = (
            "USING (SELECT :1 AS MK_DATE, :2 AS STA_NO1, :3 AS STA_NO2, "
            ":4 AS STA_NO3, :5 AS T1_STATUS FROM dual) s"
        )
        insert_cols = "INSERT (MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS)"
        insert_vals = "VALUES (s.MK_DATE, s.STA_NO1, s.STA_NO2, s.STA_NO3, s.T1_STATUS)"
    sql = (
        f"MERGE INTO {table_name} t\n"  # noqa: S608
        f"{using_clause}\n"
        "ON (t.MK_DATE = s.MK_DATE\n"
        "    AND t.STA_NO1 = s.STA_NO1\n"
        "    AND t.STA_NO2 = s.STA_NO2\n"
        "    AND t.STA_NO3 = s.STA_NO3\n"
        "    AND t.T1_STATUS = s.T1_STATUS)\n"
        "WHEN NOT MATCHED THEN\n"
        f"  {insert_cols}\n"
        f"  {insert_vals}"
    )
    return sql


def init_oracle_client_for_profiles(
    profiles: dict[str, Any], *, instant_client_dir: str
) -> ClientMode:
    needs_thick = any(p["oracle"].get("client_mode") == "thick" for p in profiles.values())
    if not needs_thick:
        return "thin"
    if not Path(instant_client_dir).exists():
        raise RuntimeError(
            f"client_mode=thick requires Instant Client at {instant_client_dir}, "
            f"but path is missing"
        )
    oracledb.init_oracle_client(lib_dir=instant_client_dir)
    return "thick"


def open_connection(profile_oracle: dict[str, Any]) -> oracledb.Connection:
    cfg = profile_oracle
    user = cfg["user"]
    password = cfg["password"]
    if cfg["auth_mode"] == "basic":
        dsn = oracledb.makedsn(cfg["host"], cfg["port"], service_name=cfg["service_name"])
        return oracledb.connect(user=user, password=password, dsn=dsn)
    if cfg["auth_mode"] == "wallet":
        kwargs: dict[str, Any] = {
            "user": user,
            "password": password,
            "dsn": cfg["dsn"],
            "config_dir": cfg["wallet_dir"],
            "wallet_location": cfg["wallet_dir"],
        }
        if cfg.get("wallet_password"):
            kwargs["wallet_password"] = cfg["wallet_password"]
        return oracledb.connect(**kwargs)
    raise ValueError(f"unknown auth_mode: {cfg['auth_mode']}")


def execute_merge(
    conn: oracledb.Connection,
    *,
    table_name: str,
    mk_date: str,
    sta_no1: str,
    sta_no2: str,
    sta_no3: str,
    t1_status: int,
    upcmpflg: int | None = None,
) -> MergeResult:
    include_flag = upcmpflg is not None
    sql = build_merge_statement(table_name=table_name, include_upcmpflg=include_flag)
    binds: tuple[Any, ...] = (mk_date, sta_no1, sta_no2, sta_no3, t1_status)
    if include_flag:
        binds = (*binds, upcmpflg)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, binds)
            rows = cur.rowcount or 0
        conn.commit()
        return MergeResult(rows_affected=rows, ora_code=None, error_message="")
    except oracledb.DatabaseError as e:
        ora_code, message = _extract_ora_code(e)
        return MergeResult(rows_affected=0, ora_code=ora_code, error_message=message)


def open_and_merge(
    profile_oracle: dict[str, Any],
    *,
    table_name: str,
    mk_date: str,
    sta_no1: str,
    sta_no2: str,
    sta_no3: str,
    t1_status: int,
    upcmpflg: int | None = None,
) -> MergeResult:
    """Open a connection, run the merge, close — never raises.

    Connection failures (e.g. DPY-6001 service-not-registered while Oracle is
    stopped) are returned as a MergeResult so the sender can route them through
    the normal retry / circuit-breaker flow instead of crashing the process.

    `upcmpflg` is the optional per-profile UPCMPFLG bind value. None omits the
    column from the INSERT entirely (table default / NULL applies).
    """
    try:
        conn = open_connection(profile_oracle)
    except oracledb.DatabaseError as e:
        ora_code, message = _extract_ora_code(e)
        return MergeResult(rows_affected=0, ora_code=ora_code, error_message=message)
    try:
        return execute_merge(
            conn,
            table_name=table_name,
            mk_date=mk_date,
            sta_no1=sta_no1,
            sta_no2=sta_no2,
            sta_no3=sta_no3,
            t1_status=t1_status,
            upcmpflg=upcmpflg,
        )
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001, S110
            pass
