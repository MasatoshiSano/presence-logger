"""AP配下の端末列挙と、インベントリとの突き合わせ。

個体の主キーは MAC にする。IPは親APのDHCPで払い出され再起動で変わり得るし、
ホスト名はSDカードのコピーで増設すると衝突する(実際に2台目投入時、両機とも
pizero2w になり MQTT の client_id が衝突して互いの接続を切断し合った)。
MACだけが個体に固定され、クローンでも複製されない。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# entry(インベントリの行) -> 過去に確認済みの正しいMAC(TOFU)を記録するファイル。
# fleet/children.conf(shellスクリプト群が共有する形式)は変更せず、fleet_ui 専用の
# runtime 状態として別に持つ。send_target_state.json と同種の「配布対象外」の扱い。
KNOWN_MACS_PATH = Path(__file__).resolve().parents[1] / "fleet" / "known_macs.json"

# ip neigh の状態。FAILED / INCOMPLETE は「そこに居ない」とみなして除外する。
# 出しておくと、存在しない端末を登録候補として操作しかける。
_PRESENT_STATES = frozenset({"REACHABLE", "STALE", "DELAY", "PROBE", "PERMANENT"})

_IPV4_RE = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")

_NEIGH_RE = re.compile(
    r"^(?P<ip>\d{1,3}(?:\.\d{1,3}){3})\s+lladdr\s+(?P<mac>[0-9a-fA-F:]{17})\s+(?P<state>\S+)"
)

AP_DEV = "wlan1"
_AP_DEV_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def current_ap_dev() -> str:
    """site.env の AP_IF と揃える。変な値は argv に渡さない。"""
    raw = (os.environ.get("AP_DEV") or AP_DEV).strip() or AP_DEV
    return raw if _AP_DEV_RE.fullmatch(raw) else AP_DEV


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
      unverified … new に見えるが、unresolved な項目があるため断定できない。
                   稼働中の子がIPドリフトでここに現れうるので登録候補にしない。
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
    *, dev: str | None = None, runner: Callable[[list[str]], str] = run_cmd
) -> list[Neighbor]:
    """AP配下の端末を列挙する。sudo は不要。"""
    return parse_neigh(
        runner(["ip", "-4", "neigh", "show", "dev", dev or current_ap_dev()])
    )


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
    neighbors: list[Neighbor],
    inventory_ips: dict[str, str | None],
    known_macs: dict[str, str] | None = None,
) -> list[FleetRow]:
    """ARPの観測結果とインベントリを突き合わせて分類する。

    IPだけでの突き合わせには2つの穴がある(最終レビューで実際に再現された):
      - Variant A: 正規のIPを別デバイスが奪い、本物は別IPに移る。IP側は
        「解決できている」ため、unresolved の判定をすり抜けて本物が new に落ちる。
      - Variant B: 2つのインベントリ項目が同じIPを指す(設定ミス・DHCPのリース
        入れ替わり)。IPだけでは2つの個体を区別できない。

    そこで known_macs(entry -> 過去に確認済みの正しいMAC)を使う。SSHの
    known_hosts と同じ信頼モデル: 初回(記録が無い)はIPでの一致を信頼し、
    以後はMACが一致するかで真贋を判定する。記録済みのMACがIPの一致する相手と
    違えば、そのIPは信用せず、ARP全体から記録済みMACを探し直す(子が別IPへ
    移動していても・IPを別デバイスに奪われていても、これで正しい個体を追える)。

    known_macs を渡さない場合は登録直後の初回同様、IPのみでの突き合わせになる
    (既存呼び出しとの後方互換)。
    """
    known_macs = known_macs or {}
    by_ip = {n.ip: n for n in neighbors}
    by_mac = {n.mac: n for n in neighbors}
    rows: list[FleetRow] = []
    claimed: set[str] = set()
    has_unresolved = False

    for entry, ip in inventory_ips.items():
        trusted_mac = known_macs.get(entry)
        candidate = by_ip.get(ip) if ip else None

        if candidate is not None and (trusted_mac is None or candidate.mac == trusted_mac):
            # IPが解決でき、かつ(未学習で信頼するか、記録済みMACと一致)。
            claimed.add(candidate.ip)
            rows.append(FleetRow(mac=candidate.mac, ip=candidate.ip, entry=entry, kind="known"))
            continue

        if trusted_mac is not None:
            moved = by_mac.get(trusted_mac)
            if moved is not None:
                # candidate があってもMACが不一致(IPを奪われている)か、
                # candidate 自体が無い(ドリフト)。記録済みの本物をARP全体から発見。
                claimed.add(moved.ip)
                rows.append(FleetRow(mac=moved.mac, ip=moved.ip, entry=entry, kind="known"))
                continue

        # 解決できない: IPに何も居ない、かつ記録済みMACもARPのどこにも無い。
        has_unresolved = True
        rows.append(FleetRow(mac=None, ip=ip, entry=entry, kind="unresolved"))

    # unresolved が1つでもある間は、残りの端末を new にせず unverified とする。
    # この危険が成立するには unresolved の存在が必要で、逆に全項目が解決していれば
    # 既知の子はすべて claim 済みなので、残りは本当に未知の端末である。
    leftover = "unverified" if has_unresolved else "new"
    for n in neighbors:
        if n.ip not in claimed:
            rows.append(FleetRow(mac=n.mac, ip=n.ip, entry=None, kind=leftover))

    return rows


def learned_macs(rows: list[FleetRow], known_macs: dict[str, str]) -> dict[str, str]:
    """classify() の結果から、新たに信頼すべき (entry, mac) を返す。

    呼び出し側がこれを既存の known_macs へマージして永続化する。classify() 自体は
    副作用を持たない純関数のまま保つため、学習の確定(ファイル書き込み)は
    呼び出し側(server.py)の責務にする。
    """
    return {
        r.entry: r.mac
        for r in rows
        if r.kind == "known" and r.entry is not None and r.mac is not None
        and r.entry not in known_macs
    }


def load_known_macs(*, path: Path = KNOWN_MACS_PATH) -> dict[str, str]:
    """記録済みの entry -> mac (TOFU) を読む。無い/壊れていれば空。"""
    if not path.exists():
        return {}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, str)}


def save_known_macs(macs: dict[str, str], *, path: Path = KNOWN_MACS_PATH) -> None:
    """entry -> mac (TOFU) を保存する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(macs, ensure_ascii=False, indent=2), encoding="utf-8")
