"""インベントリ解析の検証。

インベントリにIPは書かない（DHCPで変わるため実行時に子から取得する）。
1行1台のSSH到達名のみを持つ。
"""
import os

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/lib/deploy-common.sh"


def _read(tmp_path, content: str, check: bool = True):
    f = tmp_path / "children.conf"
    f.write_text(content, encoding="utf-8")
    return run_bash(
        f'{SOURCE}; fleet_read_inventory "{f}"', env=dict(os.environ), check=check
    )


def test_reads_plain_host_list(tmp_path):
    out = _read(tmp_path, "zero2\nzero2b\n").stdout
    assert out.splitlines() == ["zero2", "zero2b"]


def test_skips_comments_and_blank_lines(tmp_path):
    content = "# 見出し\n\nzero2\n   \n# 途中のコメント\nzero2b\n"
    assert _read(tmp_path, content).stdout.splitlines() == ["zero2", "zero2b"]


def test_strips_inline_comments_and_surrounding_space(tmp_path):
    content = "  zero2   # 1号機\nzero2b\t# 2号機\n"
    assert _read(tmp_path, content).stdout.splitlines() == ["zero2", "zero2b"]


def test_accepts_mdns_names(tmp_path):
    assert _read(tmp_path, "pizero2w.local\n").stdout.splitlines() == ["pizero2w.local"]


def test_fails_when_no_valid_host(tmp_path):
    assert _read(tmp_path, "# コメントだけ\n\n", check=False).returncode != 0


def test_fails_when_file_missing(tmp_path):
    proc = run_bash(
        f'{SOURCE}; fleet_read_inventory "{tmp_path}/nope.conf"',
        env=dict(os.environ), check=False,
    )
    assert proc.returncode != 0


def test_repository_inventory_is_parseable():
    """同梱のインベントリが常に解析可能であること。"""
    out = run_bash(f"{SOURCE}; fleet_read_inventory", env=dict(os.environ)).stdout
    assert "zero2" in out.split()
