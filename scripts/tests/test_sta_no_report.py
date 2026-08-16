"""STA_NO 重複検出の検証。

Oracle の MERGE キーは (MK_DATE, STA_NO1..3, T1_STATUS) のみで device_id を含まない
（services/bridge/src/oracle_client.py の MERGE 条件）。よって複数の子が同じ
STA_NO 三つ組を持つと、同時刻・同ステータスのレコードは片方が無警告で捨てられる。
"""
import importlib.util
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "lib" / "sta_no_report.py"

_spec = importlib.util.spec_from_file_location("sta_no_report", MODULE_PATH)
sta_no_report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sta_no_report)

find_duplicate_stations = sta_no_report.find_duplicate_stations
format_report = sta_no_report.format_report
find_malformed_hosts = sta_no_report.find_malformed_hosts


def test_no_duplicates_returns_empty():
    per_host = {
        "zero2": {"1": ["HIME", "T120", "004020"]},
        "zero2b": {"1": ["HIME", "T120", "010020"]},
    }
    assert find_duplicate_stations(per_host) == {}


def test_detects_duplicate_across_hosts():
    per_host = {
        "zero2": {"1": ["HIME", "T120", "004020"]},
        "zero2b": {"1": ["HIME", "T120", "004020"]},
    }
    dups = find_duplicate_stations(per_host)
    assert list(dups) == [("HIME", "T120", "004020")]
    assert sorted(dups[("HIME", "T120", "004020")]) == ["zero2:1", "zero2b:1"]


def test_detects_duplicate_within_one_host():
    """1台の中で2つの region が同じ STA_NO を持っても Oracle では衝突する。"""
    per_host = {"zero2": {"1": ["A", "B", "C"], "2": ["A", "B", "C"]}}
    dups = find_duplicate_stations(per_host)
    assert sorted(dups[("A", "B", "C")]) == ["zero2:1", "zero2:2"]


def test_ignores_unassigned_regions():
    """3項目すべて空の region は未割当。Picamera.py も送信対象にしない。"""
    per_host = {
        "zero2": {"1": ["", "", ""], "2": ["A", "B", "C"]},
        "zero2b": {"1": ["", "", ""]},
    }
    assert find_duplicate_stations(per_host) == {}


def test_partially_filled_region_is_counted():
    """1つでも入力があれば送信対象（Picamera.py の any(parts) と同じ判定）。"""
    per_host = {"zero2": {"1": ["A", "", ""]}, "zero2b": {"1": ["A", "", ""]}}
    assert find_duplicate_stations(per_host) == {("A", "", ""): ["zero2:1", "zero2b:1"]}


def test_report_lists_every_host_and_assignment():
    per_host = {"zero2": {"1": ["HIME", "T120", "004020"]}}
    report = format_report(per_host)
    assert "zero2" in report
    assert "HIME" in report
    assert "004020" in report


def test_report_marks_hosts_without_assignments():
    assert "未割当" in format_report({"zero2": {}})


def test_cli_exits_1_on_duplicates():
    payload = json.dumps({
        "zero2": {"1": ["A", "B", "C"]},
        "zero2b": {"1": ["A", "B", "C"]},
    })
    proc = subprocess.run(  # noqa: S603
        ["python3", str(MODULE_PATH)], input=payload,  # noqa: S607
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 1
    assert "重複" in proc.stdout


def test_cli_exits_0_when_clean():
    payload = json.dumps({"zero2": {"1": ["A", "B", "C"]}})
    proc = subprocess.run(  # noqa: S603
        ["python3", str(MODULE_PATH)], input=payload,  # noqa: S607
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0


def test_legacy_single_string_matches_list_form():
    """子の id_name_parts は旧形式の単一文字列を ["旧名","",""] とみなす。
    ここで文字単位に分解すると、同じ局の2台を見逃す。"""
    per_host = {"zero2": {"1": "HIME"}, "zero2b": {"1": ["HIME", "", ""]}}
    dups = find_duplicate_stations(per_host)
    assert sorted(dups[("HIME", "", "")]) == ["zero2:1", "zero2b:1"]


def test_none_element_is_empty_not_the_text_none():
    per_host = {
        "zero2": {"1": ["HIME", None, "004020"]},
        "zero2b": {"1": ["HIME", "", "004020"]},
    }
    dups = find_duplicate_stations(per_host)
    assert sorted(dups[("HIME", "", "004020")]) == ["zero2:1", "zero2b:1"]


def test_non_list_region_value_does_not_crash():
    assert find_duplicate_stations({"zero2": {"1": 5}}) == {}


def test_malformed_host_is_skipped_not_crashed():
    per_host = {"zero2": "not-a-dict", "zero2b": {"1": ["A", "B", "C"]}}
    assert find_duplicate_stations(per_host) == {}
    assert find_malformed_hosts(per_host) == ["zero2"]
    assert "zero2" in format_report(per_host)


def test_cli_exits_2_when_a_host_cannot_be_checked():
    payload = json.dumps({"zero2": "not-a-dict", "zero2b": {"1": ["A", "B", "C"]}})
    proc = subprocess.run(  # noqa: S603
        ["python3", str(MODULE_PATH)], input=payload,  # noqa: S607
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr


def test_cli_prefers_exit_1_when_duplicates_and_malformed_coexist():
    payload = json.dumps({
        "bad": "not-a-dict",
        "a": {"1": ["A", "B", "C"]},
        "b": {"1": ["A", "B", "C"]},
    })
    proc = subprocess.run(  # noqa: S603
        ["python3", str(MODULE_PATH)], input=payload,  # noqa: S607
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
