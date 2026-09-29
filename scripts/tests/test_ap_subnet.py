"""AP のサブネットが、この機械の別の経路・アドレスと重なるかの検出(scripts/lib/ap-subnet.sh)。

重なったまま AP を起動すると、この機械は自分を 10.42.0.1 と名乗り、別インターフェース
(例: wlan0 が繋がっている別ハブの子AP)宛ての返信が AP 側へ出て、ping も SSH も
永久に通じなくなる。Wi-Fi の関連付けは生きたままなので見た目では気づけない。
実機の経路は読まず、`ip` の出力を偽物で差し替えて検証する。
"""
import os
import stat
import textwrap
from pathlib import Path

from scripts.tests.shellhelp import run_bash

SOURCE = "source scripts/lib/ap-subnet.sh"

# 2026-09-25 の事故の状態: wlan0 が1号機の子AP(10.42.0.0/24)に居て、wlan1 はまだ無い。
INCIDENT_ROUTE = textwrap.dedent("""\
    default via 10.42.0.1 dev wlan0 proto dhcp src 10.42.0.87 metric 600
    10.42.0.0/24 dev wlan0 proto kernel scope link src 10.42.0.87 metric 600
    172.17.0.0/16 dev docker0 proto kernel scope link src 172.17.0.1 linkdown
    """)


def addr_lines(*rows: tuple[int, str, str]) -> str:
    """`ip -4 -o addr show` の出力(dev と CIDR の位置だけが読まれる)。"""
    return "".join(
        f"{idx}: {dev}    inet {cidr} scope global {dev}\\       valid_lft forever\n"
        for idx, dev, cidr in rows
    )


INCIDENT_ADDR = addr_lines(
    (1, "lo", "127.0.0.1/8"),
    (3, "wlan0", "10.42.0.87/24"),
    (7, "docker0", "172.17.0.1/16"),
)

# この機体と同じ状態: wlan1 に自分の AP、wlan0 は工場側、docker は 172.17-19。
HEALTHY_ROUTE = textwrap.dedent("""\
    default via 192.168.128.1 dev wlan0 proto dhcp src 192.168.128.148 metric 600
    10.42.0.0/24 dev wlan1 proto kernel scope link src 10.42.0.1 metric 601
    172.17.0.0/16 dev docker0 proto kernel scope link src 172.17.0.1 linkdown
    172.18.0.0/16 dev br-631d49408b8e proto kernel scope link src 172.18.0.1 linkdown
    172.19.0.0/16 dev br-80bf8fde23f1 proto kernel scope link src 172.19.0.1
    192.168.128.0/24 dev wlan0 proto kernel scope link src 192.168.128.148 metric 600
    """)
HEALTHY_ADDR = addr_lines(
    (1, "lo", "127.0.0.1/8"),
    (3, "wlan0", "192.168.128.148/24"),
    (4, "wlan1", "10.42.0.1/24"),
    (5, "br-631d49408b8e", "172.18.0.1/16"),
    (7, "docker0", "172.17.0.1/16"),
)


def _fake_ip(tmp_path: Path, route: str, addr: str, ap_if_addr: str = "") -> Path:
    """`ip -4 -o route show table main` / `ip -4 -o addr show` に偽の出力を返す。

    `ip -4 -o addr show dev X`(起動後の確認用)は ap_if_addr を返す。
    """
    (tmp_path / "route.txt").write_text(route, encoding="utf-8")
    (tmp_path / "addr.txt").write_text(addr, encoding="utf-8")
    (tmp_path / "ap_if_addr.txt").write_text(ap_if_addr, encoding="utf-8")
    script = tmp_path / "fake-ip"
    script.write_text(
        '#!/usr/bin/env bash\n'
        'case " $* " in\n'
        f'  *" route "*) cat "{tmp_path}/route.txt" ;;\n'
        f'  *" addr show dev "*) cat "{tmp_path}/ap_if_addr.txt" ;;\n'
        f'  *" addr "*)  cat "{tmp_path}/addr.txt" ;;\n'
        'esac\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _run(snippet: str, tmp_path: Path, route: str = "", addr: str = ""):
    env = dict(os.environ)
    env["AP_SUBNET_IP_CMD"] = str(_fake_ip(tmp_path, route, addr))
    return run_bash(f"{SOURCE}; {snippet}", env=env, check=False)


def test_overlap_rule_on_prefixes(tmp_path):
    def overlaps(a, b):
        return _run(f"ap_cidr_overlaps {a} {b}", tmp_path).returncode == 0

    assert overlaps("10.42.0.1/24", "10.42.0.0/24")          # 同じ範囲
    assert not overlaps("10.42.1.1/24", "10.42.0.0/24")      # 隣の範囲
    assert overlaps("10.42.0.1/24", "10.42.0.0/16")          # 広い側が狭い側を含む
    assert overlaps("10.42.0.0/16", "10.42.5.0/24")          # 逆向きでも同じ
    assert overlaps("10.42.0.1/24", "10.42.0.77/32")         # /32 が範囲の中
    assert not overlaps("10.42.0.1/24", "10.42.1.77/32")     # /32 が範囲の外


def test_incident_state_reports_the_other_interface(tmp_path):
    proc = _run("ap_subnet_conflicts 10.42.0.1 wlan1", tmp_path,
                INCIDENT_ROUTE, INCIDENT_ADDR)
    assert proc.returncode == 0
    assert "wlan0 10.42.0.0/24" in proc.stdout.splitlines()


def test_healthy_machine_reports_nothing(tmp_path):
    """自分の AP(wlan1)の経路とアドレスは重なりに数えない。再実行で誤検出しない。"""
    proc = _run("ap_subnet_conflicts 10.42.0.1 wlan1", tmp_path,
                HEALTHY_ROUTE, HEALTHY_ADDR)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_default_route_is_not_counted_as_overlap(tmp_path):
    """`default via 10.42.0.1 dev wlan0` は経路の宛先が default。ゲートウェイ値では判定しない。"""
    route = "default via 10.42.0.1 dev wlan0 proto dhcp metric 600\n"
    proc = _run("ap_subnet_conflicts 10.42.0.1 wlan1", tmp_path, route, "")
    assert proc.returncode == 0        # 関数が存在して走ったうえで、何も出ない
    assert proc.stdout.strip() == ""


def test_docker_bridge_wider_than_the_ap_range_overlaps(tmp_path):
    route = "10.42.0.0/16 dev br-abc proto kernel scope link src 10.42.0.1 linkdown\n"
    proc = _run("ap_subnet_conflicts 10.42.0.1 wlan1", tmp_path, route, "")
    assert "br-abc 10.42.0.0/16" in proc.stdout.splitlines()


def test_suggest_free_gw_skips_the_overlapping_range(tmp_path):
    proc = _run("ap_suggest_free_gw wlan1", tmp_path, INCIDENT_ROUTE, INCIDENT_ADDR)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "10.42.1.1"
