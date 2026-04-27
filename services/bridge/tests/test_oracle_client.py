from unittest.mock import MagicMock, patch

import pytest

from services.bridge.src.oracle_client import (
    MergeResult,
    build_merge_statement,
    execute_merge,
    init_oracle_client_for_profiles,
    open_connection,
)


def test_build_merge_statement_targets_correct_table():
    sql = build_merge_statement(table_name="HF1RCM01")
    assert "MERGE INTO HF1RCM01" in sql
    assert "WHEN NOT MATCHED THEN" in sql
    assert "INSERT (MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS)" in sql


def test_init_oracle_client_skips_when_all_thin():
    profiles = {
        "a": {"oracle": {"client_mode": "thin"}},
        "b": {"oracle": {"client_mode": "thin"}},
    }
    with patch("services.bridge.src.oracle_client.oracledb") as o:
        mode = init_oracle_client_for_profiles(profiles, instant_client_dir="/opt/oc")
        o.init_oracle_client.assert_not_called()
        assert mode == "thin"


def test_init_oracle_client_invokes_thick_when_any_profile_thick(tmp_path):
    profiles = {
        "a": {"oracle": {"client_mode": "thin"}},
        "b": {"oracle": {"client_mode": "thick"}},
    }
    ic_dir = tmp_path / "instantclient"
    ic_dir.mkdir()
    with patch("services.bridge.src.oracle_client.oracledb") as o:
        mode = init_oracle_client_for_profiles(profiles, instant_client_dir=str(ic_dir))
        o.init_oracle_client.assert_called_once_with(lib_dir=str(ic_dir))
        assert mode == "thick"


def test_init_oracle_client_thick_missing_dir_raises(tmp_path):
    profiles = {"b": {"oracle": {"client_mode": "thick"}}}
    with pytest.raises(RuntimeError, match="Instant Client"):
        init_oracle_client_for_profiles(
            profiles, instant_client_dir=str(tmp_path / "nonexistent")
        )


def test_open_connection_basic_mode_uses_makedsn():
    cfg = {
        "client_mode": "thin", "auth_mode": "basic", "host": "h", "port": 1521,
        "service_name": "S", "user": "u", "password": "p", "table_name": "HF1RCM01",
    }
    with patch("services.bridge.src.oracle_client.oracledb") as o:
        o.makedsn.return_value = "DSN"
        open_connection(cfg)
        o.makedsn.assert_called_once_with("h", 1521, service_name="S")
        o.connect.assert_called_once_with(user="u", password="p", dsn="DSN")


def test_open_connection_wallet_mode_uses_wallet_kwargs():
    cfg = {
        "client_mode": "thin", "auth_mode": "wallet", "dsn": "myadb_high",
        "user": "u", "password": "p", "wallet_dir": "/etc/wallets/x",
        "wallet_password": "wp", "table_name": "HF1RCM01",
    }
    with patch("services.bridge.src.oracle_client.oracledb") as o:
        open_connection(cfg)
        o.connect.assert_called_once_with(
            user="u", password="p", dsn="myadb_high",
            config_dir="/etc/wallets/x", wallet_location="/etc/wallets/x",
            wallet_password="wp",
        )


def test_open_connection_wallet_mode_omits_wallet_password_when_absent():
    cfg = {
        "client_mode": "thin", "auth_mode": "wallet", "dsn": "tcps_dsn",
        "user": "u", "password": "p", "wallet_dir": "/etc/wallets/x",
        "table_name": "HF1RCM01",
    }
    with patch("services.bridge.src.oracle_client.oracledb") as o:
        open_connection(cfg)
        kwargs = o.connect.call_args.kwargs
        assert "wallet_password" not in kwargs


def test_execute_merge_returns_rows_affected_and_no_error():
    cursor = MagicMock()
    cursor.rowcount = 1
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    result = execute_merge(
        conn, table_name="HF1RCM01",
        mk_date="20260427120000", sta_no1="001", sta_no2="A", sta_no3="01", t1_status=1,
    )
    assert isinstance(result, MergeResult)
    assert result.rows_affected == 1
    assert result.ora_code is None
    cursor.execute.assert_called_once()
    conn.commit.assert_called_once()


def test_execute_merge_captures_ora_code_on_database_error():
    cursor = MagicMock()
    err = MagicMock()
    err.code = 942
    err.message = "ORA-00942: table or view does not exist"
    db_error = type("DatabaseError", (Exception,), {})
    db_error_instance = db_error()
    db_error_instance.args = (err,)
    cursor.execute.side_effect = db_error_instance
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor

    with patch("services.bridge.src.oracle_client.oracledb") as o:
        o.DatabaseError = db_error
        result = execute_merge(
            conn, table_name="HF1RCM01",
            mk_date="20260427120000", sta_no1="001", sta_no2="A", sta_no3="01", t1_status=1,
        )
    assert result.rows_affected == 0
    assert result.ora_code == 942
    assert "ORA-00942" in result.error_message
