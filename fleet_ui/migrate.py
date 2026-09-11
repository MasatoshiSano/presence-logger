"""既存の子を旧ハブからこのハブへ付け替える。

新機登録ウィザードは STA_NO を空にしホスト名を変える。稼働中の子を移すときに
それを流すと記録が止まる。こちらは WiFi・SSH鍵・インベントリだけを付け替え、
ホスト名と局番号はそのままにする。

新ハブから旧親の AP 配下へは直接届かない。工場網で旧親へ SSH し、旧親から
子へ SSH する。
"""
from __future__ import annotations

import re
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

from fleet_ui.hostname import validate_hostname
from fleet_ui.provision import StepResult, add_to_inventory, register_host_key, wait_for_return

_IPV4_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)$"
)
_HOST_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?$")
_MAC_RE = re.compile(r"(?i)^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")
_PUBKEY_RE = re.compile(
    r"^(ssh-(?:ed25519|rsa)|ecdsa-sha2-nistp256) [A-Za-z0-9+/=]+(?: .*)?$"
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REMOTE_INVENTORY = "~/projects/presence-logger/fleet/children.conf"


def validate_old_host(host: str) -> str | None:
    """問題があれば日本語の理由。ssh の argv に素で渡す値なので厳しくする。"""
    if not host or not host.strip():
        return "旧親のアドレスを入力してください"
    host = host.strip()
    if host.startswith("-") or "@" in host or "/" in host or " " in host:
        return "旧親のアドレスに使えない文字があります"
    if any(c in host for c in ";|&$()`<>\"'\\"):
        return "旧親のアドレスに使えない文字があります"
    if _IPV4_RE.match(host):
        return None
    if _HOST_RE.match(host) and ".." not in host:
        return None
    return "旧親のアドレスは IPv4 かホスト名で指定してください"


def inventory_name(entry: str) -> str:
    entry = entry.strip()
    return entry if entry.endswith(".local") else f"{entry}.local"


def parse_children_conf(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        s = line.split("#", 1)[0].strip()
        if s:
            out.append(s)
    return out


def strip_inventory_entry(text: str, entry: str) -> str:
    keep = []
    for line in text.splitlines(keepends=True):
        raw = line[:-1] if line.endswith("\n") else line
        if raw.split("#", 1)[0].strip() == entry:
            continue
        keep.append(line)
    return "".join(keep)


def _env_file_value(path: Path, key: str) -> str:
    if not path.is_file():
        return ""
    prefix = f"{key}="
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            # PSK に # が含まれることがあるので、行末コメントとしては切らない。
            val = line[len(prefix):].strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            return val
    return ""


def run_cmd_long(cmd: list[str], timeout: int = 45) -> str:
    """入れ子 SSH と nmcli は 10 秒では足りない。失敗しても空文字を返す。"""
    try:
        r = subprocess.run(  # noqa: S603 (fixed argv, no shell)
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout


def load_ap_join(repo: Path | None = None) -> tuple[str, str]:
    """SSID と AP パスワード。PSK は画面へ出さない。"""
    repo = repo or REPO_ROOT
    ssid = _env_file_value(repo / ".kit" / "ap-join.env", "AP_SSID")
    psk = _env_file_value(repo / ".kit" / "ap-join.env", "WIFI_AP_PSK")
    if not ssid:
        ssid = _env_file_value(repo / "site.env", "AP_SSID")
    if not psk:
        psk = _env_file_value(repo / ".kit" / "secrets.env", "WIFI_AP_PSK")
    return ssid, psk


def read_pubkey(path: Path | None = None) -> str:
    path = path or (Path.home() / ".ssh" / "id_ed25519.pub")
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def migrate_status(*, repo: Path | None = None, pubkey_path: Path | None = None) -> dict:
    ssid, psk = load_ap_join(repo)
    return {
        "pubkey": read_pubkey(pubkey_path),
        "ap_ssid": ssid,
        "has_psk": bool(psk),
    }


def ssh_pi(
    host: str,
    remote: str,
    *,
    runner: Callable[[list[str]], str] = run_cmd_long,
) -> str:
    return runner([
        "ssh",
        "-o", "ConnectTimeout=8",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        f"pi@{host}",
        remote,
    ])


def list_remote_children(
    old_host: str,
    *,
    runner: Callable[[list[str]], str] = run_cmd_long,
    remote_inventory: str = DEFAULT_REMOTE_INVENTORY,
) -> StepResult:
    err = validate_old_host(old_host)
    if err:
        return StepResult(ok=False, message=err)
    text = ssh_pi(old_host, f"cat {remote_inventory}", runner=runner)
    if not text.strip():
        pubkey = read_pubkey()
        hint = ""
        if pubkey:
            hint = (
                " 旧親へ一度だけ公開鍵を入れてください: "
                f"ssh-copy-id -i ~/.ssh/id_ed25519.pub pi@{old_host}"
            )
        return StepResult(
            ok=False,
            message="旧親の子一覧を読めませんでした。工場網で SSH できるか確認してください。"
            + hint,
            output=text,
        )
    entries = parse_children_conf(text)
    return StepResult(ok=True, message=f"{len(entries)} 台", output="\n".join(entries))


def _nested_ssh(entry: str, inner: str) -> str:
    return (
        "ssh -o ConnectTimeout=8 -o BatchMode=yes "
        f"pi@{shlex.quote(entry)} {shlex.quote(inner)}"
    )


def _extract_mac(text: str) -> str:
    for raw in text.split():
        cand = raw.strip().lower()
        if _MAC_RE.match(cand):
            return cand
    return ""


def take_child(
    old_host: str,
    entry: str,
    *,
    repo: Path | None = None,
    pubkey: str = "",
    runner: Callable[[list[str]], str] = run_cmd_long,
    wait_fn: Callable[..., StepResult] = wait_for_return,
    known_hosts: Path | None = None,
    inventory_path: Path | None = None,
    remote_inventory: str = DEFAULT_REMOTE_INVENTORY,
) -> StepResult:
    """1台を旧親からこのハブへ移す。局番号・ホスト名は触らない。"""
    repo = repo or REPO_ROOT
    err = validate_old_host(old_host)
    if err:
        return StepResult(ok=False, message=err)
    entry = (entry or "").strip()
    if not entry or any(c in entry for c in ";|&$()`<>\"'\\ \t"):
        return StepResult(ok=False, message="子の名前が不正です")

    inv_file = inventory_path or (repo / "fleet" / "children.conf")
    existing = ""
    if inv_file.exists():
        existing = inv_file.read_text(encoding="utf-8")
    short = entry[: -len(".local")] if entry.endswith(".local") else entry
    dup = validate_hostname(short, parse_children_conf(existing))
    if dup:
        return StepResult(ok=False, message=dup)

    pubkey = pubkey or read_pubkey()
    if not pubkey or not _PUBKEY_RE.match(pubkey.split("\n", 1)[0]):
        return StepResult(ok=False, message="このハブの SSH 公開鍵がありません")
    ssid, psk = load_ap_join(repo)
    if not ssid or len(psk) < 8:
        return StepResult(
            ok=False,
            message="このハブの AP 名またはパスワードが読めません。.kit/ap-join.env を確認してください",
        )

    mac_inner = (
        "cat /sys/class/net/wlan0/address 2>/dev/null || "
        "cat /sys/class/net/wlan1/address 2>/dev/null"
    )
    mac_out = ssh_pi(old_host, _nested_ssh(entry, mac_inner), runner=runner)
    mac = _extract_mac(mac_out)
    if not mac:
        return StepResult(
            ok=False,
            message=f"{entry} の MAC が取れませんでした。旧親からその子へ SSH できるか確認してください",
            output=mac_out,
        )

    key_inner = (
        "umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; "
        f"grep -qxF {shlex.quote(pubkey)} ~/.ssh/authorized_keys || "
        f"printf '%s\\n' {shlex.quote(pubkey)} >> ~/.ssh/authorized_keys"
    )
    ssh_pi(old_host, _nested_ssh(entry, key_inner), runner=runner)

    wifi_inner = (
        f"sudo nmcli -w 15 dev wifi connect {shlex.quote(ssid)} "
        f"password {shlex.quote(psk)}"
    )
    ssh_pi(old_host, _nested_ssh(entry, wifi_inner), runner=runner)

    waited = wait_fn(mac)
    if not waited.ok:
        return StepResult(
            ok=False,
            message=(
                f"{entry} がこのハブの AP ({ssid}) に現れません。"
                f" {waited.message}"
            ),
            output=waited.output,
        )

    inv_name = inventory_name(entry)
    kh = register_host_key(inv_name, runner=runner, known_hosts=known_hosts)
    if not kh.ok:
        return kh
    added = add_to_inventory(inv_name, path=inv_file)
    if not added.ok:
        return added

    remote_text = ssh_pi(old_host, f"cat {remote_inventory}", runner=runner)
    stripped = strip_inventory_entry(remote_text, entry)
    write_remote = f"printf %s {shlex.quote(stripped)} > {remote_inventory}"
    ssh_pi(old_host, write_remote, runner=runner)

    return StepResult(
        ok=True,
        message=f"{entry} をこのハブへ移しました（ホスト名と局番号はそのまま）",
        output=waited.output,
    )


def list_children_payload(old_host: str, **kwargs) -> dict:
    r = list_remote_children(old_host, **kwargs)
    entries = parse_children_conf(r.output) if r.ok else []
    return {
        "ok": r.ok,
        "message": r.message,
        "children": [{"entry": e, "name": inventory_name(e)} for e in entries],
        "pubkey": read_pubkey(),
    }


def take_payload(old_host: str, entry: str, **kwargs) -> dict:
    r = take_child(old_host, entry, **kwargs)
    return {"ok": r.ok, "message": r.message, "output": r.output}
