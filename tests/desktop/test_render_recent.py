import importlib.util
from pathlib import Path

# ハイフン付きディレクトリのスクリプトをファイルパスから読み込む
_P = Path(__file__).resolve().parents[2] / "desktop" / "presence-tools" / "_render_recent.py"
_spec = importlib.util.spec_from_file_location("_render_recent", _P)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
render = _mod.render


def test_render_shows_raw_t1_status_not_enter_exit():
    text = (
        "count=2\nora_code=\nerror_message=\n"
        "row=20260717090000,HIME,ABC,001,1,0\n"
        "row=20260717090005,HIME,ABC,001,11,0\n"
    )
    out = render(text)
    assert "ENTER" not in out and "EXIT" not in out
    assert "🟢" not in out and "🔴" not in out
    # 生の数値が出る（1 と 11）
    assert "20260717090000" not in out  # 整形済み日時になる
    assert "2026-07-17 09:00:00" in out
    assert " 1 " in out or "\t1" in out or "  1  " in out  # T1=1 が数値で見える
    assert "11" in out


def test_render_reports_error():
    out = render("count=0\nora_code=12170\nerror_message=timeout\n")
    assert "12170" in out


def test_render_empty():
    out = render("count=0\nora_code=\nerror_message=\n")
    assert "0" in out  # 0件でも壊れない
