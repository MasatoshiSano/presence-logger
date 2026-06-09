from unittest.mock import MagicMock, patch

import pytest

from services.bridge.src.oracle_client import (
    MergeResult,
    build_merge_statement,
    execute_merge,
    init_oracle_client_for_profiles,
    open_and_merge,
    open_connection,
)


def test_build_merge_statement_without_upcmpflg_default():
    sql = build_merge_statement(table_name="HF1RCM01")
    assert "MERGE INTO HF1RCM01" in sql
    assert "INSERT (MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS)" in sql
    # UPCMPFLG must NOT appear at all when the profile omits the upcmpflg key.
    assert "UPCMPFLG" not in sql
    # No WHEN MATCHED clause -- existing rows must be left untouched.
    assert "WHEN MATCHED" not in sql


def test_build_merge_statement_with_upcmpflg_uses_bind_variable():
    sql = build_merge_statement(table_name="HF1RCM01", include_upcmpflg=True)
    assert "MERGE INTO HF1RCM01" in sql
    assert "INSERT (MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS, UPCMPFLG)" in sql
    # 6th bind variable is the UPCMPFLG value -- never a literal, so the same
    # template works for profiles wanting UPCMPFLG=0, 1, 2, ...
    assert ":6 AS UPCMPFLG" in sql
    assert "s.T1_STATUS, s.UPCMPFLG)" in sql
    assert "WHEN MATCHED" not in sql


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
    # No upcmpflg passed -> 5 binds only, no UPCMPFLG in SQL.
    sql_arg, binds_arg = cursor.execute.call_args.args
    assert "UPCMPFLG" not in sql_arg
    assert binds_arg == ("20260427120000", "001", "A", "01", 1)
    conn.commit.assert_called_once()


def test_execute_merge_binds_upcmpflg_value_when_provided():
    cursor = MagicMock()
    cursor.rowcount = 1
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    execute_merge(
        conn, table_name="HF1RCM01",
        mk_date="20260427120000", sta_no1="001", sta_no2="A", sta_no3="01", t1_status=1,
        upcmpflg=1,
    )
    sql_arg, binds_arg = cursor.execute.call_args.args
    assert "INSERT (MK_DATE, STA_NO1, STA_NO2, STA_NO3, T1_STATUS, UPCMPFLG)" in sql_arg
    # 6 binds, with UPCMPFLG value last.
    assert binds_arg == ("20260427120000", "001", "A", "01", 1, 1)


def test_execute_merge_upcmpflg_zero_is_distinct_from_none():
    """upcmpflg=0 must include the column (with value 0), NOT be treated as 'omit'."""
    cursor = MagicMock()
    cursor.rowcount = 1
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    execute_merge(
        conn, table_name="HF1RCM01",
        mk_date="m", sta_no1="1", sta_no2="2", sta_no3="3", t1_status=1,
        upcmpflg=0,
    )
    sql_arg, binds_arg = cursor.execute.call_args.args
    assert "UPCMPFLG" in sql_arg
    assert binds_arg[-1] == 0


def test_execute_merge_captures_ora_code_on_database_error():
    cursor = MagicMock()
    err = MagicMock()
    err.code = 942
    err.full_code = "ORA-00942"
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


def test_execute_merge_maps_dpy_6001_to_ora_12514():
    """DPY-6001 (thin-mode service-not-registered) should surface as ORA-12514
    so the existing permanent_ora_codes list trips the circuit breaker."""
    cursor = MagicMock()
    err = MagicMock()
    err.code = 0
    err.full_code = "DPY-6001"
    err.message = 'Service "x" is not registered with the listener'
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
    assert result.ora_code == 12514


def test_open_and_merge_returns_merge_result_when_connection_fails():
    """Bug fix: when Oracle ADB is stopped, open_connection raises DPY-6001;
    bridge used to crash. open_and_merge must absorb the exception so the
    sender can route it through the retry/circuit-breaker flow."""
    cfg = {
        "client_mode": "thin", "auth_mode": "wallet", "dsn": "x",
        "user": "u", "password": "p", "wallet_dir": "/w",
    }
    err = MagicMock()
    err.code = 0
    err.full_code = "DPY-6001"
    err.message = 'Service "x" is not registered with the listener'
    db_error = type("DatabaseError", (Exception,), {})
    db_error_instance = db_error()
    db_error_instance.args = (err,)

    with patch("services.bridge.src.oracle_client.oracledb") as o:
        o.DatabaseError = db_error
        o.connect.side_effect = db_error_instance
        result = open_and_merge(
            cfg, table_name="HF1RCM01",
            mk_date="20260427120000", sta_no1="001", sta_no2="A", sta_no3="01", t1_status=1,
        )
    assert isinstance(result, MergeResult)
    assert result.rows_affected == 0
    assert result.ora_code == 12514
    assert "not registered" in result.error_message


def test_open_and_merge_calls_execute_merge_and_closes_connection_on_success():
    cfg = {
        "client_mode": "thin", "auth_mode": "basic", "host": "h", "port": 1521,
        "service_name": "S", "user": "u", "password": "p",
    }
    conn = MagicMock()
    cursor = MagicMock()
    cursor.rowcount = 1
    conn.cursor.return_value.__enter__.return_value = cursor

    with patch("services.bridge.src.oracle_client.oracledb") as o:
        o.makedsn.return_value = "DSN"
        o.connect.return_value = conn
        result = open_and_merge(
            cfg, table_name="HF1RCM01",
            mk_date="20260427120000", sta_no1="001", sta_no2="A", sta_no3="01", t1_status=1,
        )
    assert result.rows_affected == 1
    assert result.ora_code is None
    conn.close.assert_called_once()


def test_open_and_merge_closes_connection_when_merge_raises():
    """Connection must close even if execute_merge encounters a non-DatabaseError
    (e.g. programmer error). Ensures no leaked Oracle connections under stress."""
    cfg = {
        "client_mode": "thin", "auth_mode": "basic", "host": "h", "port": 1521,
        "service_name": "S", "user": "u", "password": "p",
    }
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = RuntimeError("boom")
    conn.cursor.return_value.__enter__.return_value = cursor

    db_error = type("DatabaseError", (Exception,), {})
    with patch("services.bridge.src.oracle_client.oracledb") as o:
        o.DatabaseError = db_error
        o.makedsn.return_value = "DSN"
        o.connect.return_value = conn
        with pytest.raises(RuntimeError, match="boom"):
            open_and_merge(
                cfg, table_name="HF1RCM01",
                mk_date="20260427120000", sta_no1="001", sta_no2="A", sta_no3="01", t1_status=1,
            )
    conn.close.assert_called_once()
