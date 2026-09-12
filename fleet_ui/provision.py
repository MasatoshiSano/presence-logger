"""新しい子の登録6工程。

docs/DEPLOY.md の10工程のうち、1(既存確認)は一覧表示そのもの、2(電源投入)は物理作業、
9-10(STA_NO設定と掃除)は本UIの非ゴールなので、ここが担うのは6工程。

対象は必ずIPで指定する。SDカードのコピーで増設すると2台が同名になるため、
ホスト名で指定するとどちらを操作しているか判別できない。
"""
from __future__ import annotations

import re
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fleet_ui.discovery import parse_neigh, run_cmd
from fleet_ui.hostname import validate_hostname

# ホスト名として安全な形。sed/printf へ素で埋め込むため、ここを緩めてはいけない。
_SAFE_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?\Z")

INVENTORY = Path("fleet/children.conf")

STEPS: list[tuple[str, str]] = [
    ("stop", "送信を止める"),
    ("blank", "STA_NO を退避して空にする"),
    ("rename", "ホスト名を変更して再起動"),
    ("mdns", "mDNS を正常化"),
    ("hostkey", "host key を登録"),
    ("inventory", "インベントリに追加"),
]


@dataclass(frozen=True)
class StepResult:
    ok: bool
    message: str
    output: str = ""


def _ssh(ip: str, remote: str, runner: Callable[[list[str]], str]) -> str:
    return runner([
        "ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", f"pi@{ip}", remote
    ])


def _guarded(fn: Callable[[], str], ok_msg: str) -> StepResult:
    """失敗を例外で飛ばさず StepResult に畳む。自動では次へ進ませないため。"""
    try:
        out = fn()
    except Exception as e:  # 外部コマンドの失敗は例外にせず画面へ出す
        return StepResult(ok=False, message=f"失敗: {e}", output="")
    return StepResult(ok=True, message=ok_msg, output=out)


def stop_publisher(ip: str, *, runner: Callable[[list[str]], str] = run_cmd) -> StepResult:
    """工程1: 送信を止める。

    クローンだと MQTT の client_id が既存機と同一になり、ブローカーが
    既存接続を切断するため2台が互いを蹴り合う。まずこれを止める。
    """
    return _guarded(
        lambda: _ssh(ip, "sudo systemctl stop child-csv-to-mqtt.service", runner),
        "送信を停止しました",
    )


def blank_sta_no(ip: str, *, runner: Callable[[list[str]], str] = run_cmd) -> StepResult:
    """工程2: STA_NO を退避して空にする。

    誤った局番号のまま送信させないため。現場の値が決まるまでは無記録が安全。
    """
    remote = (
        "cp -a ~/id_names_config.json ~/id_names_config.json.clone-backup && "
        "python3 -c \"import json;p='/home/pi/id_names_config.json';"
        "d=json.load(open(p));"
        "json.dump({'id_names':{k:['','',''] for k in d['id_names']}},open(p,'w'))\""
    )
    return _guarded(lambda: _ssh(ip, remote, runner), "STA_NO を空にしました")


def rename_and_reboot(
    ip: str, new_hostname: str, *, runner: Callable[[list[str]], str] = run_cmd
) -> StepResult:
    """工程3: ホスト名を変更して再起動する。

    ホスト名が device_id と MQTT client_id の両方を決める。ここが分かれれば競合は終わる。
    """
    # 呼び出し側の検証に依存せず、この関数自身で不正な値を弾く。
    # new_hostname は sed の置換文字列と printf の中へ *素で* 埋め込まれるため、
    # 引用符やスラッシュが混ざると子の上で任意のコマンドが走る。
    # (例: `x/"; touch /tmp/PWNED; echo "` は sed の二重引用符から抜ける)
    # server.py は validate_hostname で先に弾くが、公開関数として自衛しておく。
    if not _SAFE_HOSTNAME_RE.match(new_hostname):
        return StepResult(
            ok=False,
            message=f"ホスト名に使えない文字が含まれています: {new_hostname!r}",
        )
    q = shlex.quote(new_hostname)
    # /etc/hosts は「127.0.1.1 の行を丸ごと差し替える」形で更新する。
    #
    # 旧ホスト名を使って置換してはいけない。`\b` はハイフンの手前でも単語境界に
    # なるため、旧名 pizero2w の置換が pizero2w-2 の行にも当たり
    # pizero2w-3-2 のように別ホストの行を壊す(ローカルで再現済み)。
    # 127.0.1.1 行はローカルホスト名専用なので、丸ごと置き換えれば旧名に依存せず、
    # set-hostname との順序も問題にならない。
    remote = (
        f"sudo hostnamectl set-hostname {q} && "
        "if grep -q '^127\\.0\\.1\\.1[[:space:]]' /etc/hosts; then "
        f'sudo sed -i "s/^127\\.0\\.1\\.1[[:space:]].*/127.0.1.1\\t{new_hostname}/" /etc/hosts; '
        "else "
        f"printf '127.0.1.1\\t{new_hostname}\\n' | sudo tee -a /etc/hosts >/dev/null; "
        "fi; "
        "sudo reboot"
    )
    return _guarded(lambda: _ssh(ip, remote, runner), f"{new_hostname} へ変更し再起動しました")


def wait_for_return(
    mac: str,
    *,
    timeout_s: int = 180,
    runner: Callable[[list[str]], str] = run_cmd,
    sleeper: Callable[[float], None] = time.sleep,
) -> StepResult:
    """工程3の後: MACで再出現を待つ。

    再起動でDHCPのIPが変わり得るため、IPやホスト名では追えない。MACだけが頼り。
    """
    mac = mac.lower()
    waited = 0
    while waited < timeout_s:
        for n in parse_neigh(runner(["ip", "-4", "neigh", "show", "dev", "wlan1"])):
            if n.mac == mac:
                return StepResult(ok=True, message=f"復帰を確認しました ({n.ip})", output=n.ip)
        sleeper(5)
        waited += 5
    return StepResult(ok=False, message=f"{timeout_s}秒待っても復帰しませんでした")


def restart_mdns(ips: list[str], *, runner: Callable[[list[str]], str] = run_cmd) -> StepResult:
    """工程4: mDNS を正常化する。

    クローンが既存の名前を奪うと、既存機の avahi が衝突回避で自分を自動改名する。
    全機を再announceさせて元に戻す。
    """
    def _do() -> str:
        outs = []
        for ip in ips:
            outs.append(_ssh(ip, "sudo systemctl restart avahi-daemon", runner))
        return "\n".join(outs)

    return _guarded(_do, f"{len(ips)}台の mDNS を再announceしました")


def register_host_key(
    hostname: str, *, runner: Callable[[list[str]], str] = run_cmd,
    known_hosts: Path | None = None,
) -> StepResult:
    """工程5: host key を登録する(親側のみ、sudo不要)。

    登録しないと fleet-status.sh が「到達できません」になる。
    """
    dest = known_hosts or (Path.home() / ".ssh" / "known_hosts")

    def _do() -> str:
        out = runner(["ssh-keyscan", "-H", hostname])
        if out.strip():
            with dest.open("a", encoding="utf-8") as f:
                f.write(out if out.endswith("\n") else out + "\n")
        return out

    return _guarded(_do, f"{hostname} の host key を登録しました")


def add_to_inventory(entry: str, *, path: Path = INVENTORY) -> StepResult:
    """工程6: インベントリへ追記する(親側のみ、sudo不要)。

    既に載っていれば何もしない。二重登録すると同じ子へ二重配布してしまう。
    """
    def _do() -> str:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        lines = [ln.split("#", 1)[0].strip() for ln in existing.splitlines()]
        if entry in lines:
            return "既に登録済みです"
        sep = "" if existing.endswith("\n") or not existing else "\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{sep}{entry}\n")
        return f"{entry} を追加しました"

    return _guarded(_do, "インベントリを更新しました")


def probe_hostname(
    ip: str, *, runner: Callable[[list[str]], str] = run_cmd
) -> str:
    """このハブの鍵で SSH できるか。できなければ空文字。

    未登録のクローンは known_hosts に居ないので accept-new にする。
    """
    if not ip or any(c in ip for c in " ;|&$()`<>\"'\\"):
        return ""
    return runner([
        "ssh",
        "-o", "ConnectTimeout=3",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        f"pi@{ip}",
        "hostname",
    ]).strip()


def adopt_keeping_identity(
    ip: str,
    *,
    existing: list[str],
    runner: Callable[[list[str]], str] = run_cmd,
    known_hosts: Path | None = None,
    inventory_path: Path | None = None,
) -> StepResult:
    """AP に既に居る子を、ホスト名と局番号を変えずに取り込む。

    新機登録ウィザードはクローン増設向け（STA_NO を空にする / 改名）。
    既存の子のSDを別ハブへ持ってきたときはそれを流してはいけない。
    """
    hostname = probe_hostname(ip, runner=runner)
    if not hostname:
        return StepResult(
            ok=False,
            message=(
                "このハブの鍵では SSH できません。"
                "旧親が工場網にいるなら「他のハブから引き継ぐ」。"
                "旧親が無い・別工場なら、クローンした子SDを"
                "「子SDをこのハブ用にする」で書いてから起動してください。"
            ),
        )
    err = validate_hostname(hostname, existing)
    if err:
        return StepResult(ok=False, message=err)

    inv_name = hostname if hostname.endswith(".local") else f"{hostname}.local"
    kh = register_host_key(inv_name, runner=runner, known_hosts=known_hosts)
    if not kh.ok:
        return kh
    added = add_to_inventory(inv_name, path=inventory_path or INVENTORY)
    if not added.ok:
        return added
    return StepResult(
        ok=True,
        message=f"{hostname} をこのハブへ取り込みました（ホスト名と局番号はそのまま）",
        output=kh.output,
    )
