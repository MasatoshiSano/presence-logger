"""Unit tests for main._OracleAdapter dispatch by client_mode.

The adapter is the single point that routes to either python-oracledb
(thin/thick via oracle_client.open_and_merge) or the oracle-jdbc sidecar
(via oracle_jdbc_client.execute_merge_via_jdbc). The rest of the bridge
sender pipeline never sees client_mode -- it just calls
execute_merge_for_profile and consumes a MergeResult.
"""
from __future__ import annotations

from unittest.mock import patch

from services.bridge.src.main import _OracleAdapter
from services.bridge.src.oracle_client import MergeResult

_JDBC_CFG = {
    "url": "http://oracle-jdbc:8086",
    "connect_timeout_ms": 12345,
    "read_timeout_ms": 67890,
}


def _profile(client_mode: str, **overrides):
    base = {
        "oracle": {
            "client_mode": client_mode,
            "auth_mode": "basic",
            "host": "10.168.252.16",
            "port": 1521,
            "service_name": "HHS001",
            "user": "ZHH001",
            "password": "ZHH001_99",
            "table_name": "HF1RCM01",
        }
    }
    base["oracle"].update(overrides)
    return base


def test_adapter_routes_jdbc_client_mode_to_sidecar():
    adapter = _OracleAdapter(jdbc_cfg=_JDBC_CFG)
    sentinel = MergeResult(rows_affected=1, ora_code=None, error_message="")
    with patch("services.bridge.src.main.execute_merge_via_jdbc",
               return_value=sentinel) as via_jdbc, \
         patch("services.bridge.src.main.open_and_merge") as via_oracledb:
        result = adapter.execute_merge_for_profile(
            profile=_profile("jdbc"),
            mk_date="2026-06-04 14:30",
            sta_no1="001", sta_no2="002", sta_no3="003",
            t1_status=1,
        )
    assert result is sentinel
    via_oracledb.assert_not_called()
    via_jdbc.assert_called_once()
    # Per-request fields and the bridge.yaml-supplied timeouts both make it.
    kw = via_jdbc.call_args.kwargs
    assert kw["proxy_url"] == "http://oracle-jdbc:8086"
    assert kw["table_name"] == "HF1RCM01"
    assert kw["mk_date"] == "2026-06-04 14:30"
    assert kw["t1_status"] == 1
    assert kw["connect_timeout_ms"] == 12345
    assert kw["read_timeout_ms"] == 67890


def test_adapter_routes_thin_client_mode_to_python_oracledb():
    adapter = _OracleAdapter(jdbc_cfg=_JDBC_CFG)
    sentinel = MergeResult(rows_affected=1, ora_code=None, error_message="")
    with patch("services.bridge.src.main.open_and_merge",
               return_value=sentinel) as via_oracledb, \
         patch("services.bridge.src.main.execute_merge_via_jdbc") as via_jdbc:
        result = adapter.execute_merge_for_profile(
            profile=_profile("thin"),
            mk_date="m", sta_no1="1", sta_no2="2", sta_no3="3", t1_status=2,
        )
    assert result is sentinel
    via_jdbc.assert_not_called()
    via_oracledb.assert_called_once()


def test_adapter_routes_thick_client_mode_to_python_oracledb():
    """Thick mode shares the python-oracledb code path with Thin -- the
    sidecar must never be touched for it."""
    adapter = _OracleAdapter(jdbc_cfg=_JDBC_CFG)
    with patch("services.bridge.src.main.open_and_merge",
               return_value=MergeResult(rows_affected=0, ora_code=None, error_message="")) \
         as via_oracledb, \
         patch("services.bridge.src.main.execute_merge_via_jdbc") as via_jdbc:
        adapter.execute_merge_for_profile(
            profile=_profile("thick"),
            mk_date="m", sta_no1="1", sta_no2="2", sta_no3="3", t1_status=1,
        )
    via_jdbc.assert_not_called()
    via_oracledb.assert_called_once()


def test_adapter_forwards_profile_upcmpflg_to_jdbc_path():
    adapter = _OracleAdapter(jdbc_cfg=_JDBC_CFG)
    profile = _profile("jdbc")
    profile["oracle"]["upcmpflg"] = 1
    with patch("services.bridge.src.main.execute_merge_via_jdbc",
               return_value=MergeResult(rows_affected=1, ora_code=None, error_message="")) \
         as via_jdbc:
        adapter.execute_merge_for_profile(
            profile=profile,
            mk_date="m", sta_no1="1", sta_no2="2", sta_no3="3", t1_status=1,
        )
    assert via_jdbc.call_args.kwargs["upcmpflg"] == 1


def test_adapter_forwards_profile_upcmpflg_to_python_oracledb_path():
    adapter = _OracleAdapter(jdbc_cfg=_JDBC_CFG)
    profile = _profile("thin")
    profile["oracle"]["upcmpflg"] = 0
    with patch("services.bridge.src.main.open_and_merge",
               return_value=MergeResult(rows_affected=1, ora_code=None, error_message="")) \
         as via_oracledb:
        adapter.execute_merge_for_profile(
            profile=profile,
            mk_date="m", sta_no1="1", sta_no2="2", sta_no3="3", t1_status=1,
        )
    # upcmpflg=0 must be preserved (not collapsed to None).
    assert via_oracledb.call_args.kwargs["upcmpflg"] == 0


def test_adapter_omits_upcmpflg_when_profile_does_not_set_it():
    adapter = _OracleAdapter(jdbc_cfg=_JDBC_CFG)
    profile = _profile("jdbc")  # no upcmpflg in oracle section
    with patch("services.bridge.src.main.execute_merge_via_jdbc",
               return_value=MergeResult(rows_affected=1, ora_code=None, error_message="")) \
         as via_jdbc:
        adapter.execute_merge_for_profile(
            profile=profile,
            mk_date="m", sta_no1="1", sta_no2="2", sta_no3="3", t1_status=1,
        )
    assert via_jdbc.call_args.kwargs["upcmpflg"] is None


def test_adapter_propagates_ora_code_from_sidecar_for_breaker():
    """The circuit breaker's permanent_ora_codes list must trip on JDBC failures
    the same way it trips on python-oracledb failures, so the adapter must
    pass the ora_code from the sidecar through unchanged."""
    adapter = _OracleAdapter(jdbc_cfg=_JDBC_CFG)
    fail = MergeResult(rows_affected=0, ora_code=1017,
                       error_message="ORA-01017: invalid username/password")
    with patch("services.bridge.src.main.execute_merge_via_jdbc", return_value=fail):
        result = adapter.execute_merge_for_profile(
            profile=_profile("jdbc"),
            mk_date="m", sta_no1="1", sta_no2="2", sta_no3="3", t1_status=1,
        )
    assert result.ora_code == 1017
    assert "ORA-01017" in result.error_message
