"""Tests for the oracle-jdbc sidecar HTTP client."""
from __future__ import annotations

import io
import urllib.error
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from services.bridge.src.oracle_client import MergeResult
from services.bridge.src.oracle_jdbc_client import (
    _build_jdbc_url,
    _parse_kv_body,
    execute_merge_via_jdbc,
)


def _profile(host: str = "10.166.5.93", svc: str = "HHC001") -> dict[str, Any]:
    return {
        "client_mode": "jdbc",
        "auth_mode": "basic",
        "host": host,
        "port": 1521,
        "service_name": svc,
        "user": "ZHH001",
        "password": "ZHH001_99",
        "table_name": "HF1RCM01",
    }


def test_build_jdbc_url_matches_oracle_thin_format():
    url = _build_jdbc_url(_profile())
    assert url == "jdbc:oracle:thin:@10.166.5.93:1521/HHC001"


def test_parse_kv_body_handles_blank_lines_and_missing_keys():
    body = "rows_affected=1\n\nora_code=\nerror_message=ok\n"
    out = _parse_kv_body(body)
    assert out == {"rows_affected": "1", "ora_code": "", "error_message": "ok"}


class _FakeResponse:
    def __init__(self, body: str):
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


_OK_BODY = "rows_affected=1\nora_code=\nerror_message=\n"
_URLOPEN_TARGET = "services.bridge.src.oracle_jdbc_client.urllib.request.urlopen"


@contextmanager
def _patched_urlopen(captured: dict[str, Any], response_body: str = _OK_BODY):
    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["data"] = request.data
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return _FakeResponse(response_body)

    with patch(_URLOPEN_TARGET, side_effect=fake_urlopen):
        yield


def test_execute_merge_via_jdbc_posts_form_with_all_required_fields():
    captured: dict[str, Any] = {}
    with _patched_urlopen(captured):
        result = execute_merge_via_jdbc(
            _profile(),
            proxy_url="http://oracle-jdbc:8086",
            table_name="HF1RCM01",
            mk_date="2026-06-04 14:30",
            sta_no1="001", sta_no2="002", sta_no3="003",
            t1_status=1,
        )
    assert result == MergeResult(rows_affected=1, ora_code=None, error_message="")
    assert captured["url"] == "http://oracle-jdbc:8086/merge"
    assert captured["method"] == "POST"
    # Headers from urllib are title-cased.
    assert any(h.lower() == "content-type" and v == "application/x-www-form-urlencoded"
               for h, v in captured["headers"].items())
    body = captured["data"].decode("utf-8")
    for needle in (
        "url=jdbc%3Aoracle%3Athin%3A%4010.166.5.93%3A1521%2FHHC001",
        "user=ZHH001",
        "password=ZHH001_99",
        "table_name=HF1RCM01",
        "mk_date=2026-06-04+14%3A30",
        "sta_no1=001", "sta_no2=002", "sta_no3=003",
        "t1_status=1",
        "connect_timeout_ms=10000",
        "read_timeout_ms=30000",
    ):
        assert needle in body, f"missing form field: {needle}"


def test_execute_merge_via_jdbc_omits_upcmpflg_value_when_none():
    """Default behaviour: the form does NOT carry upcmpflg_value, so the
    sidecar builds an INSERT that does not touch the UPCMPFLG column."""
    captured: dict[str, Any] = {}
    with _patched_urlopen(captured):
        execute_merge_via_jdbc(
            _profile(), proxy_url="http://oracle-jdbc:8086",
            table_name="HF1RCM01", mk_date="m",
            sta_no1="1", sta_no2="2", sta_no3="3", t1_status=1,
            # upcmpflg not passed
        )
    body = captured["data"].decode("utf-8")
    assert "upcmpflg_value=" not in body


def test_execute_merge_via_jdbc_includes_upcmpflg_value_when_int():
    captured: dict[str, Any] = {}
    with _patched_urlopen(captured):
        execute_merge_via_jdbc(
            _profile(), proxy_url="http://oracle-jdbc:8086",
            table_name="HF1RCM01", mk_date="m",
            sta_no1="1", sta_no2="2", sta_no3="3", t1_status=1,
            upcmpflg=1,
        )
    body = captured["data"].decode("utf-8")
    assert "upcmpflg_value=1" in body


def test_execute_merge_via_jdbc_upcmpflg_zero_is_sent():
    captured: dict[str, Any] = {}
    with _patched_urlopen(captured):
        execute_merge_via_jdbc(
            _profile(), proxy_url="http://oracle-jdbc:8086",
            table_name="HF1RCM01", mk_date="m",
            sta_no1="1", sta_no2="2", sta_no3="3", t1_status=1,
            upcmpflg=0,
        )
    body = captured["data"].decode("utf-8")
    assert "upcmpflg_value=0" in body


def test_execute_merge_via_jdbc_strips_trailing_slash_on_proxy_url():
    captured: dict[str, Any] = {}
    with _patched_urlopen(captured):
        execute_merge_via_jdbc(
            _profile(), proxy_url="http://oracle-jdbc:8086/",
            table_name="T", mk_date="m", sta_no1="1", sta_no2="2", sta_no3="3", t1_status=1,
        )
    assert captured["url"] == "http://oracle-jdbc:8086/merge"


def test_execute_merge_via_jdbc_propagates_ora_code():
    body = "rows_affected=0\nora_code=942\nerror_message=ORA-00942: table or view does not exist\n"
    with _patched_urlopen({}, response_body=body):
        result = execute_merge_via_jdbc(
            _profile(), proxy_url="http://oracle-jdbc:8086",
            table_name="HF1RCM01", mk_date="m", sta_no1="1", sta_no2="2", sta_no3="3", t1_status=1,
        )
    assert result.rows_affected == 0
    assert result.ora_code == 942
    assert "ORA-00942" in result.error_message


def test_execute_merge_via_jdbc_http_error_returns_merge_result_without_ora_code():
    def raising(*_a, **_k):
        raise urllib.error.HTTPError(
            "http://oracle-jdbc:8086/merge", 500, "Internal Server Error", {}, io.BytesIO(b""),
        )
    target = "services.bridge.src.oracle_jdbc_client.urllib.request.urlopen"
    with patch(target, side_effect=raising):
        result = execute_merge_via_jdbc(
            _profile(), proxy_url="http://oracle-jdbc:8086",
            table_name="T", mk_date="m", sta_no1="1", sta_no2="2", sta_no3="3", t1_status=1,
        )
    assert result.rows_affected == 0
    assert result.ora_code is None
    assert "HTTP 500" in result.error_message


def test_execute_merge_via_jdbc_url_error_returns_merge_result_without_ora_code():
    def raising(*_a, **_k):
        raise urllib.error.URLError("connection refused")
    target = "services.bridge.src.oracle_jdbc_client.urllib.request.urlopen"
    with patch(target, side_effect=raising):
        result = execute_merge_via_jdbc(
            _profile(), proxy_url="http://oracle-jdbc:8086",
            table_name="T", mk_date="m", sta_no1="1", sta_no2="2", sta_no3="3", t1_status=1,
        )
    assert result.rows_affected == 0
    assert result.ora_code is None
    assert "unreachable" in result.error_message


def test_execute_merge_via_jdbc_timeout_returns_merge_result_without_ora_code():
    def raising(*_a, **_k):
        raise TimeoutError("read timed out")
    target = "services.bridge.src.oracle_jdbc_client.urllib.request.urlopen"
    with patch(target, side_effect=raising):
        result = execute_merge_via_jdbc(
            _profile(), proxy_url="http://oracle-jdbc:8086",
            table_name="T", mk_date="m", sta_no1="1", sta_no2="2", sta_no3="3", t1_status=1,
        )
    assert result.ora_code is None
    assert "unreachable" in result.error_message


def test_execute_merge_via_jdbc_garbled_body_yields_zero_rows_and_no_code():
    with _patched_urlopen({}, response_body="oops, not key=value at all"):
        result = execute_merge_via_jdbc(
            _profile(), proxy_url="http://oracle-jdbc:8086",
            table_name="T", mk_date="m", sta_no1="1", sta_no2="2", sta_no3="3", t1_status=1,
        )
    assert result.rows_affected == 0
    assert result.ora_code is None
