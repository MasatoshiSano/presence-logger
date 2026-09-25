"""フェーズ20(基盤)を検証する。

python3-yaml はシステムの python3 に要る(install.sh・connect-hime-h-reap.sh・
show-recent-records.sh・pipeline_monitor が venv ではなくシステム側で yaml を
読むため)。ここが抜けると後段が全部落ちる。
"""
import os

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/bootstrap/20-base-packages.sh"


def _no_docker_ce(fake_bin):
    """素の Pi を装う。このテストを走らせる親機には docker-ce が入っている。"""
    fake_bin("dpkg-query", 'exit 1')


def _has_docker_ce(fake_bin):
    fake_bin("dpkg-query",
             '[ "${@: -1}" = docker-ce ] && { printf "install ok installed"; exit 0; }; exit 1')


def test_package_list_has_the_non_obvious_ones(fake_bin):
    _no_docker_ce(fake_bin)
    out = run_bash(f'{SOURCE}; base_packages', env=dict(os.environ)).stdout.split()
    for pkg in ("docker.io", "docker-cli", "docker-compose", "python3-yaml",
                "mosquitto-clients", "dkms", "linux-headers-rpi-2712"):
        assert pkg in out, f"{pkg} が不足"


def test_package_list_has_no_name_missing_from_trixie(fake_bin):
    """apt-get install は1つでも名前が見つからないと、何も入れずに失敗する。

    raspberrypi-kernel-headers は bookworm までの名前で、trixie(Debian 13)には
    無い。これが並びに1つ混じっているだけで、素の Pi OS ではフェーズ20 が
    必ず落ち、docker も python3-yaml も入らない。Pi 5 のヘッダは
    linux-headers-rpi-2712。
    """
    _no_docker_ce(fake_bin)
    out = run_bash(f'{SOURCE}; base_packages', env=dict(os.environ)).stdout.split()
    assert "raspberrypi-kernel-headers" not in out
    # Docker 社の apt リポジトリにしか無い名前。素の Pi OS には無い。
    for pkg in ("docker-compose-plugin", "docker-ce", "docker-ce-cli", "containerd.io"):
        assert pkg not in out, f"{pkg} は素の Pi では見つからない"


def test_docker_ce_already_installed_is_left_alone(fake_bin):
    """docker.io は docker-ce と Conflicts。並びに入れると apt が docker-ce を外す。

    親機は docker-ce で動いている([実測] apt-get -s install docker.io が
    "4 to remove")。フェーズ20 を流し直しただけで、本番のコンテナが
    載っているエンジンが入れ替わってしまう。docker-ce が既にあるなら
    docker 一式は頼まない。
    """
    _has_docker_ce(fake_bin)
    out = run_bash(f'{SOURCE}; base_packages', env=dict(os.environ)).stdout.split()
    for pkg in ("docker.io", "docker-cli", "docker-compose"):
        assert pkg not in out
    assert "python3-yaml" in out


def test_headers_for_the_running_kernel_are_requested_separately(tmp_path, fake_bin):
    _no_docker_ce(fake_bin)
    """DKMS が要るのは「今動いているカーネル」のヘッダ。

    linux-headers-rpi-2712 はアーカイブ最新版のヘッダしか連れてこない。
    書いたばかりの SD は古いカーネルで動いているので、それだけでは
    /lib/modules/$(uname -r)/build が無く、フェーズ30 の DKMS が落ちる。
    ただし旧版がアーカイブから消えていることもあるので、本体の並びには
    混ぜず(混ぜると全部入らない)、別の呼び出しにして失敗を本体に波及させない。
    """
    fake_bin("uname", 'echo 6.18.29+rpt-rpi-2712')
    fake_bin("apt-get", 'printf "apt-get %s\\n" "$*" >> "$FAKE_LOG"; '
                        '[[ "$*" == *6.18.29* ]] && exit 100; exit 0')
    r = run_bash(f'{SOURCE}; base_install_packages', env=dict(os.environ), check=False)
    assert r.returncode == 0, r.stderr
    calls = fake_bin.log.read_text(encoding="utf-8").splitlines()
    main_call = next(c for c in calls if "python3-yaml" in c)
    assert "6.18.29" not in main_call
    assert any("linux-headers-6.18.29+rpt-rpi-2712" in c for c in calls)
    assert "6.18.29+rpt-rpi-2712" in r.stderr     # 入らなかったことは黙らない


def test_base_package_failure_is_not_swallowed(fake_bin):
    _no_docker_ce(fake_bin)
    fake_bin("uname", 'echo 6.18.29+rpt-rpi-2712')
    fake_bin("apt-get", 'printf "apt-get %s\\n" "$*" >> "$FAKE_LOG"; exit 100')
    r = run_bash(f'{SOURCE}; base_install_packages', env=dict(os.environ), check=False)
    assert "python3-yaml" in fake_bin.log.read_text(encoding="utf-8")
    assert r.returncode != 0


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
