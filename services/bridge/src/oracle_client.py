import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import oracledb

_log = logging.getLogger("bridge.oracle")

ClientMode = Literal["thin", "thick"]


@dataclass
class MergeResult:
    rows_affected: int
    ora_code: int | None
    error_message: str


def build_merge_statement(*, table_name: str) -> str:
    using_clause = (
        "USING (SELECT :1 AS MK_DATE, :2 AS STA_NO1, :3 AS STA_NO2, "
        ":4 AS STA_NO3, :5 AS T1_STATUS FROM dual) s"
    )
    # table_name comes from validated config, not user input
    sql = (
        f"MERGE INTO {table_name} t\n"  # noqa: S608
        f"{using_clause}\n"
        "ON (t.MK_DATE = s.MK_DATE\n"
        "    AND t.STA_NO1 = s.STA_NO1\n"
        "    AND t.STA_NO2 = s.STA_NO2\n"
        "    AND t.STA_NO3 = s.STA_NO3\n"
        "    AND t.T1_STATUS = s.T1_STATUS)\n"
        "WHEN NOT MATCHED THEN\n"
        "  INSERT (MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS)\n"
        "  VALUES (s.MK_DATE, s.STA_NO1, s.STA_NO2, s.STA_NO3, s.T1_STATUS)"
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
) -> MergeResult:
    sql = build_merge_statement(table_name=table_name)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (mk_date, sta_no1, sta_no2, sta_no3, t1_status))
            rows = cur.rowcount or 0
        conn.commit()
        return MergeResult(rows_affected=rows, ora_code=None, error_message="")
    except oracledb.DatabaseError as e:
        ora_code = None
        message = str(e)
        if e.args and hasattr(e.args[0], "code"):
            ora_code = int(e.args[0].code)
            message = str(getattr(e.args[0], "message", "") or message)
        return MergeResult(rows_affected=0, ora_code=ora_code, error_message=message)
