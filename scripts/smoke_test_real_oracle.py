#!/usr/bin/env python3
"""Real-Oracle smoke test for presence-logger.

Reads real credentials from /home/pi/Downloads/db_connection_history.csv (NOT in repo)
and exercises the bridge's Oracle client + Sender pipeline against the configured DB.

Usage:
  .venv/bin/python scripts/smoke_test_real_oracle.py

Modes:
- If the on-prem Oracle (10.168.252.16:1521/HHS001) is reachable, performs SELECT 1
  followed by an idempotent MERGE with clearly-test-marked STA values, then deletes
  the test row to leave HF1RCM01 untouched.
- If the host is unreachable, runs the full bridge pipeline (Sender → Oracle adapter)
  with a mocked oracledb.connect to verify call-shape, SQL text, and bind parameters.
"""
from __future__ import annotations

import csv
import socket
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CSV_PATH = Path("/home/pi/Downloads/db_connection_history.csv")


def load_onprem_creds() -> dict[str, str]:
    """Return the current (on-prem direct) connection profile from the CSV."""
    with CSV_PATH.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["接続方式"] == "オンプレミス直接接続":
                return {
                    "host": row["ホスト"],
                    "port": int(row["ポート"]),
                    "service_name": row["サービス名"],
                    "user": row["ユーザー名"],
                    "password": row["パスワード"],
                    "table_name": row["テーブル名"],
                }
    raise SystemExit("on-prem profile not found in CSV")


def is_reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def build_profile(creds: dict[str, str]) -> dict:
    return {
        "description": "smoke test (on-prem)",
        "sntp": {"servers": ["ntp.nict.jp"]},
        "oracle": {
            "client_mode": "thin",
            "auth_mode": "basic",
            "host": creds["host"],
            "port": creds["port"],
            "service_name": creds["service_name"],
            "user": creds["user"],
            "password": creds["password"],
            "table_name": creds["table_name"],
        },
    }


def run_live_test(creds: dict) -> int:
    print(f"[live] connecting to {creds['host']}:{creds['port']}/{creds['service_name']} as {creds['user']}")
    from services.bridge.src.oracle_client import (
        build_merge_statement, execute_merge, open_connection,
    )

    cfg = build_profile(creds)["oracle"]
    conn = open_connection(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM dual")
            row = cur.fetchone()
            assert row[0] == 1, f"unexpected SELECT 1 result: {row}"
            print("[live] SELECT 1 FROM dual -> OK")

            cur.execute(f"SELECT COUNT(*) FROM {cfg['table_name']}")
            count = cur.fetchone()[0]
            print(f"[live] {cfg['table_name']} row count = {count}")

        # Use clearly-test-marker STA values that won't collide with production rows.
        test_mk_date = "20991231235959"
        merge_args = dict(
            mk_date=test_mk_date,
            sta_no1="TST", sta_no2="T", sta_no3="00",
            t1_status=1,
        )
        result = execute_merge(conn, table_name=cfg["table_name"], **merge_args)
        print(f"[live] MERGE result: rows_affected={result.rows_affected}, ora_code={result.ora_code}")
        if result.ora_code is not None:
            print(f"[live] MERGE error: {result.error_message}")
            return 2

        # Idempotency: re-run, expect rows_affected=0.
        result2 = execute_merge(conn, table_name=cfg["table_name"], **merge_args)
        assert result2.rows_affected == 0, f"expected idempotent MERGE, got {result2.rows_affected}"
        print("[live] MERGE idempotent re-run -> rows_affected=0 OK")

        # Cleanup: remove the test row.
        with conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {cfg['table_name']} WHERE MK_DATE=:1 AND STA_NO1=:2 AND STA_NO2=:3 AND STA_NO3=:4 AND T1_STATUS=:5",
                (test_mk_date, "TST", "T", "00", 1),
            )
            deleted = cur.rowcount
        conn.commit()
        print(f"[live] cleanup DELETE -> {deleted} row(s) removed")
        return 0
    finally:
        conn.close()


def run_offline_test(creds: dict) -> int:
    """Verify the bridge pipeline routes correctly using mocked oracledb.connect."""
    print(f"[offline] {creds['host']}:{creds['port']} unreachable; running bridge-pipeline check with mocked oracledb")
    from services.bridge.src.oracle_client import (
        build_merge_statement, open_connection,
    )

    sql = build_merge_statement(table_name=creds["table_name"])
    expected_keywords = ["MERGE INTO HF1RCM01", "WHEN NOT MATCHED THEN", "INSERT (MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS)"]
    for kw in expected_keywords:
        assert kw in sql, f"missing keyword in MERGE SQL: {kw!r}"
    print("[offline] MERGE statement contains all expected keywords")

    cfg = build_profile(creds)["oracle"]
    with patch("services.bridge.src.oracle_client.oracledb") as mock_db:
        mock_db.makedsn.return_value = "FAKE_DSN"
        mock_db.connect.return_value = MagicMock()
        open_connection(cfg)
        mock_db.makedsn.assert_called_once_with(creds["host"], creds["port"], service_name=creds["service_name"])
        mock_db.connect.assert_called_once()
        kwargs = mock_db.connect.call_args.kwargs
        assert kwargs["user"] == creds["user"]
        assert kwargs["password"] == creds["password"]
        assert kwargs["dsn"] == "FAKE_DSN"
    print("[offline] open_connection() called oracledb.connect with correct host/port/service/user/password")
    print("[offline] OK — bridge will produce the expected Oracle calls when factory WiFi is reachable")
    return 0


def main() -> int:
    creds = load_onprem_creds()
    print(f"loaded creds for: {creds['user']}@{creds['host']}:{creds['port']}/{creds['service_name']} (table={creds['table_name']})")
    if is_reachable(creds["host"], creds["port"]):
        return run_live_test(creds)
    return run_offline_test(creds)


if __name__ == "__main__":
    sys.exit(main())
