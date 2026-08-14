"""AP配下の端末列挙と、インベントリとの突き合わせ。

個体の主キーは MAC にする。IPは親APのDHCPで払い出され再起動で変わり得るし、
ホスト名はSDカードのコピーで増設すると衝突する(実際に2台目投入時、両機とも
pizero2w になり MQTT の client_id が衝突して互いの接続を切断し合った)。
MACだけが個体に固定され、クローンでも複製されない。
"""
from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

# ip neigh の状態。FAILED / INCOMPLETE は「そこに居ない」とみなして除外する。
# 出しておくと、存在しない端末を登録候補として操作しかける。
_PRESENT_STATES = frozenset({"REACHABLE", "STALE", "DELAY", "PROBE", "PERMANENT"})

_IPV4_RE = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")

_NEIGH_RE = re.compile(
    r"^(?P<ip>\d{1,3}(?:\.\d{1,3}){3})\s+lladdr\s+(?P<mac>[0-9a-fA-F:]{17})\s+(?P<state>\S+)"
)

AP_DEV = "wlan1"


@dataclass(frozen=True)
class Neighbor:
    """AP配下で観測された1台。"""
    ip: str
    mac: str
    state: str


@dataclass(frozen=True)
class FleetRow:
    """画面に出す1行。

    kind:
      known      … インベントリに載っていてAPにも居る
      new        … APに居るがインベントリのどのIPとも一致しない(登録候補)
      unresolved … インベントリにあるがIPを解決できない/APに居ない
                   **新機扱いにしない**。既存の子を新機と誤認すると、
                   登録ウィザードで稼働中の機体を再プロビジョニングしかねない。
    """
    mac: str | None
    ip: str | None
    entry: str | None
    kind: str


def run_cmd(cmd: list[str]) -> str:
    """外部コマンドを実行して標準出力を返す。失敗しても例外にせず空文字を返す。"""
    try:
        r = subprocess.run(  # noqa: S603 (fixed argv, no shell)
            cmd, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout


def read_neighbors(
    *, dev: str = AP_DEV, runner: Callable[[list[str]], str] = run_cmd
) -> list[Neighbor]:
    """AP配下の端末を列挙する。sudo は不要。"""
    return parse_neigh(runner(["ip", "-4", "neigh", "show", "dev", dev]))


def parse_neigh(text: str) -> list[Neighbor]:
    """`ip -4 neigh show dev wlan1` の出力を解析する。解析できない行は捨てる。"""
    out: list[Neighbor] = []
    for line in text.splitlines():
        m = _NEIGH_RE.match(line.strip())
        if m is None:
            continue
        state = m.group("state").upper()
        if state not in _PRESENT_STATES:
            continue
        out.append(Neighbor(ip=m.group("ip"), mac=m.group("mac").lower(), state=state))
    return out


def resolve_inventory_ips(
    entries: list[str], *, runner: Callable[[list[str]], str] = run_cmd
) -> dict[str, str | None]:
    """インベントリの各エントリをIPへ解決する。解決できなければ None。

    解決経路は2つ必要:
      - mDNS名(`pizero2w-2.local`) … `getent hosts`
      - ssh_config の別名(`zero2`) … `ssh -G` の hostname 行
    """
    out: dict[str, str | None] = {}
    for e in entries:
        ip = None
        first = runner(["getent", "hosts", e]).split()
        if first and _IPV4_RE.fullmatch(first[0]):
            ip = first[0]
        if ip is None:
            for line in runner(["ssh", "-G", e]).splitlines():
                if line.startswith("hostname "):
                    cand = line.split(None, 1)[1].strip()
                    if _IPV4_RE.fullmatch(cand):
                        ip = cand
                    break
        out[e] = ip
    return out


def classify(
    neighbors: list[Neighbor], inventory_ips: dict[str, str | None]
) -> list[FleetRow]:
    """ARPの観測結果とインベントリを突き合わせて分類する。"""
    by_ip = {n.ip: n for n in neighbors}
    rows: list[FleetRow] = []
    claimed: set[str] = set()

    for entry, ip in inventory_ips.items():
        n = by_ip.get(ip) if ip else None
        if n is None:
            rows.append(FleetRow(mac=None, ip=ip, entry=entry, kind="unresolved"))
            continue
        claimed.add(n.ip)
        rows.append(FleetRow(mac=n.mac, ip=n.ip, entry=entry, kind="known"))

    for n in neighbors:
        if n.ip not in claimed:
            rows.append(FleetRow(mac=n.mac, ip=n.ip, entry=None, kind="new"))

    return rows
