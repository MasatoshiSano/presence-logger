"""bootstrap-hub.sh のフェーズ振り分けを検証する。

フェーズを飛ばせること・順序が番号どおりであることは、途中で失敗した作業を
再開する上での前提になる。番号順が崩れると、AP を上げる前に compose を
起動するなど「順序が意味を持つ」工程が壊れる。
"""
import os

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/bootstrap-hub.sh"


def _phases(tmp_path):
    d = tmp_path / "bootstrap"
    d.mkdir()
    for name in ("70-desktop.sh", "10-japanese-input.sh", "40-configs.sh"):
        p = d / name
        p.write_text("#!/usr/bin/env bash\necho ran $0\n", encoding="utf-8")
        p.chmod(0o755)
    return d


def _phases_with_leading_zeros(tmp_path):
    # leading zero（08, 09）の octal 誤読を guard する 10# prefix をテストするため
    d = tmp_path / "bootstrap"
    d.mkdir()
    for name in ("08-locale.sh", "09-time.sh", "10-japanese-input.sh"):
        p = d / name
        p.write_text("#!/usr/bin/env bash\necho ran $0\n", encoding="utf-8")
        p.chmod(0o755)
    return d


def test_phases_are_listed_in_numeric_order(tmp_path):
    d = _phases(tmp_path)
    out = run_bash(f'{SOURCE}; bootstrap_discover_phases "{d}"',
                   env=dict(os.environ)).stdout.split()
    assert [os.path.basename(p) for p in out] == [
        "10-japanese-input.sh", "40-configs.sh", "70-desktop.sh"]


def test_single_phase_selection(tmp_path):
    d = _phases(tmp_path)
    out = run_bash(f'{SOURCE}; bootstrap_select_phases "{d}" 40 40',
                   env=dict(os.environ)).stdout.split()
    assert [os.path.basename(p) for p in out] == ["40-configs.sh"]


def test_range_selection_is_inclusive(tmp_path):
    d = _phases(tmp_path)
    out = run_bash(f'{SOURCE}; bootstrap_select_phases "{d}" 40 70',
                   env=dict(os.environ)).stdout.split()
    assert [os.path.basename(p) for p in out] == ["40-configs.sh", "70-desktop.sh"]


def test_no_range_selects_everything(tmp_path):
    d = _phases(tmp_path)
    out = run_bash(f'{SOURCE}; bootstrap_select_phases "{d}"',
                   env=dict(os.environ)).stdout.split()
    # 引数なしで全フェーズが番号順に選択されることを確認
    assert [os.path.basename(p) for p in out] == [
        "10-japanese-input.sh", "40-configs.sh", "70-desktop.sh"]


def test_sourcing_does_not_execute_main(tmp_path):
    # source しただけで root チェックや apt が走らないこと(テスト可能性の前提)
    proc = run_bash(f'{SOURCE}; echo SOURCED_OK', env=dict(os.environ), check=False)
    assert proc.returncode == 0
    assert "SOURCED_OK" in proc.stdout


def test_leading_zeros_are_not_treated_as_octal(tmp_path):
    # 10# prefix がなければ 08 / 09 は bash で "value too great for base" になる
    d = _phases_with_leading_zeros(tmp_path)
    out = run_bash(f'{SOURCE}; bootstrap_select_phases "{d}" 08 10',
                   env=dict(os.environ)).stdout.split()
    assert [os.path.basename(p) for p in out] == [
        "08-locale.sh", "09-time.sh", "10-japanese-input.sh"]
