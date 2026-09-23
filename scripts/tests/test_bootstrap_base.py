"""フェーズ20(基盤)を検証する。

python3-yaml はシステムの python3 に要る(install.sh・connect-hime-h-reap.sh・
show-recent-records.sh・pipeline_monitor が venv ではなくシステム側で yaml を
読むため)。ここが抜けると後段が全部落ちる。
"""
import os

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/bootstrap/20-base-packages.sh"


def test_package_list_has_the_non_obvious_ones():
    out = run_bash(f'{SOURCE}; base_packages', env=dict(os.environ)).stdout.split()
    for pkg in ("docker.io", "docker-compose-plugin", "python3-yaml",
                "mosquitto-clients", "dkms", "raspberrypi-kernel-headers"):
        assert pkg in out, f"{pkg} が不足"


def test_hostname_files_are_rewritten(tmp_path):
    hn = tmp_path / "hostname"
    hn.write_text("raspberrypi5\n", encoding="utf-8")
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1\tlocalhost\n127.0.1.1\traspberrypi5 raspberrypi5\n",
                     encoding="utf-8")
    run_bash(f'{SOURCE}; base_set_hostname presence-hub-2 "{hn}" "{hosts}"',
             env=dict(os.environ))
    assert hn.read_text(encoding="utf-8").strip() == "presence-hub-2"
    body = hosts.read_text(encoding="utf-8")
    assert "127.0.1.1\tpresence-hub-2" in body
    assert "127.0.0.1\tlocalhost" in body          # 他行を壊さない
    assert "raspberrypi5" not in body


def test_hosts_line_is_added_when_absent(tmp_path):
    hn = tmp_path / "hostname"
    hn.write_text("old\n", encoding="utf-8")
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1\tlocalhost\n", encoding="utf-8")
    run_bash(f'{SOURCE}; base_set_hostname presence-hub-2 "{hn}" "{hosts}"',
             env=dict(os.environ))
    assert "127.0.1.1\tpresence-hub-2" in hosts.read_text(encoding="utf-8")


def test_hosts_line_with_hyphen_is_replaced_whole(tmp_path):
    hn = tmp_path / "hostname"
    hn.write_text("pizero2w\n", encoding="utf-8")
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1\tlocalhost\n127.0.1.1\tpizero2w-2 pizero2w-2\n",
                     encoding="utf-8")
    run_bash(f'{SOURCE}; base_set_hostname pizero2w-3 "{hn}" "{hosts}"',
             env=dict(os.environ))
    body = hosts.read_text(encoding="utf-8")
    assert "127.0.1.1\tpizero2w-3" in body
    assert "pizero2w-2" not in body
    assert "127.0.0.1\tlocalhost" in body
    # `\b` 置換は pizero2w-2 を pizero2w-3-2 にし、上の部分一致をすり抜ける。
    host_lines = [ln for ln in body.splitlines() if ln.startswith("127.0.1.1")]
    assert host_lines == ["127.0.1.1\tpizero2w-3"]


def test_venv_creation_is_skipped_when_present(tmp_path, fake_bin):
    fake_bin("python3", 'printf "python3 %s\\n" "$*" >> "$FAKE_LOG"')
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    py = tmp_path / ".venv" / "bin" / "python"
    py.touch()
    py.chmod(0o755)
    run_bash(f'{SOURCE}; base_ensure_venv "{tmp_path}"', env=dict(os.environ))
    assert "venv" not in fake_bin.log.read_text(encoding="utf-8")
