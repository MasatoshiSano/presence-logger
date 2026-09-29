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
import uuid
from collections.abc import Callable
from pathlib import Path

from fleet_ui.hostname import validate_hostname
from fleet_ui.provision import StepResult, add_to_inventory, register_host_key, wait_for_return
from fleet_ui.send_target import align_send_target, ip_in_subnet, load_ap_gw_ip

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
CAT_OK = "__PRESENCE_CAT_OK__"
MISSING = "__PRESENCE_MISSING__"
KEY_OK = "KEY_OK"
WIFI_OK = "WIFI_OK"


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


def run_cmd_long(
    cmd: list[str], timeout: int = 45, input_text: str | None = None
) -> str:
    """入れ子 SSH と nmcli は 10 秒では足りない。失敗しても空文字を返す。"""
    try:
        r = subprocess.run(  # noqa: S603 (fixed argv, no shell)
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
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
    runner: Callable[..., str] = run_cmd_long,
    input_text: str | None = None,
) -> str:
    cmd = [
        "ssh",
        "-o", "ConnectTimeout=8",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        f"pi@{host}",
        remote,
    ]
    try:
        return runner(cmd, input_text=input_text)
    except TypeError:
        return runner(cmd)


def _read_remote_inventory(
    old_host: str,
    remote_inventory: str,
    *,
    runner: Callable[[list[str]], str],
) -> tuple[str | None, str]:
    """旧親の children.conf を読む。失敗と空ファイルを区別する。

    run_cmd_long は終了コードを捨てるので、sentinel が付くまで成功とみなさない。
    失敗なのに空文字を書き戻すと、旧親の名簿が消える。
    """
    remote = (
        f"if test -f {remote_inventory}; then "
        f"cat {remote_inventory}; printf '\\n{CAT_OK}\\n'; "
        f"else echo {MISSING}; fi"
    )
    out = ssh_pi(old_host, remote, runner=runner) or ""
    lines = [ln for ln in out.splitlines() if ln]
    if lines and lines[-1] == MISSING and CAT_OK not in out:
        return "", "missing"
    if CAT_OK not in out:
        return None, "fail"
    return out[: out.rfind(CAT_OK)], "ok"


def list_remote_children(
    old_host: str,
    *,
    runner: Callable[[list[str]], str] = run_cmd_long,
    remote_inventory: str = DEFAULT_REMOTE_INVENTORY,
) -> StepResult:
    err = validate_old_host(old_host)
    if err:
        return StepResult(ok=False, message=err)
    text, status = _read_remote_inventory(old_host, remote_inventory, runner=runner)
    if status != "ok":
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
            output=text or "",
        )
    entries = parse_children_conf(text or "")
    return StepResult(ok=True, message=f"{len(entries)} 台", output="\n".join(entries))


def _nested_ssh(entry: str, inner: str) -> str:
    return (
        "ssh -o ConnectTimeout=8 -o BatchMode=yes "
        "-o StrictHostKeyChecking=accept-new "
        f"pi@{shlex.quote(entry)} {shlex.quote(inner)}"
    )


def _extract_mac(text: str) -> str:
    for raw in text.split():
        cand = raw.strip().lower()
        if _MAC_RE.match(cand):
            return cand
    return ""


_PRINTABLE = re.compile(r"^[\x20-\x7e]+$")
_HEX64 = re.compile(r"^[0-9A-Fa-f]{64}$")


def psk_valid(psk: str) -> bool:
    """WPA-PSK の規則(表示可能ASCII 8〜63文字、または16進64文字)。

    外れた値は AP 側でも使えないので、書く前に止める。"""
    return bool(_HEX64.match(psk)) or (8 <= len(psk) <= 63 and bool(_PRINTABLE.match(psk)))


def nm_escape_psk(psk: str) -> str:
    """keyfile は引用符を特別扱いしない。囲むと引用符ごと PSK になる(2026-09-25)。

    バックスラッシュは二重に、先頭の空白は1文字ごとに \\s(NM は先頭空白を黙って落とす)。
    書式は `nmcli --offline connection add` の出力に合わせる。scripts/prepare-child-sd.sh と
    同じ規則(別実装)。テストベクタは scripts/tests/nm_vectors.py。"""
    v = psk.replace("\\", "\\\\")
    stripped = v.lstrip(" ")
    return "\\s" * (len(v) - len(stripped)) + stripped


def nm_ssid(ssid: str) -> str:
    """nmcli と同じ: 素直な ASCII は文字列形式、それ以外はバイト列形式。"""
    raw = ssid.encode("utf-8")
    if not 1 <= len(raw) <= 32:
        raise ValueError("ssid length must be 1..32 bytes")
    if b"\x00" in raw:
        raise ValueError("ssid must not contain NUL")
    if _PRINTABLE.match(ssid) and "\\" not in ssid and ssid[0] != " " and ssid[-1] != " ":
        return ssid.replace(";", "\\\\;")
    return "".join(f"{b};" for b in raw)


def nm_join_keyfile(ssid: str, psk: str) -> str:
    """子へ渡す NM 接続ファイル。PSK を argv に載せないための中身。"""
    return (
        "[connection]\n"
        "id=presence-hub-join\n"
        f"uuid={uuid.uuid4()}\n"
        "type=wifi\n"
        "autoconnect=true\n"
        "autoconnect-priority=200\n"
        # SD 側と同じ。既定の4回で諦めると電源再投入まで戻らない(2026-09-23)。
        "autoconnect-retries=0\n"
        "\n"
        "[wifi]\n"
        "mode=infrastructure\n"
        f"ssid={nm_ssid(ssid)}\n"
        "\n"
        "[wifi-security]\n"
        "key-mgmt=wpa-psk\n"
        f"psk={nm_escape_psk(psk)}\n"
        "\n"
        "[ipv4]\n"
        "method=auto\n"
        "\n"
        "[ipv6]\n"
        "method=ignore\n"
    )


# 切替は SSH が切れても止まらないよう systemd-run の独立ユニットで走らせる。
# 順序は「up が成功してから他プロファイルの自動接続を切る」。先に切ると、up が失敗した
# とき子はどこにも繋がらない孤立状態になる。失敗時は新プロファイルを切って元の Wi-Fi に戻す。
# WIFI_OK はユニット側(journalctl -u presence-hub-join-switch)に出る。SSH には届かない。
WIFI_INSTALL = """
umask 077
tmp=$(mktemp)
cat > "$tmp" || exit 1
sudo install -m 600 "$tmp" /etc/NetworkManager/system-connections/presence-hub-join.nmconnection
rm -f "$tmp"
sudo nmcli connection reload
sw=$(mktemp)
cat > "$sw" <<'SWITCH'
prev=$(nmcli -t -f NAME,TYPE connection show --active 2>/dev/null | while IFS=: read -r n t; do
  [ "$t" = "802-11-wireless" ] || [ "$t" = "wifi" ] || continue
  [ "$n" = "presence-hub-join" ] && continue
  echo "$n"; break
done)
if nmcli -w 15 connection up presence-hub-join; then
  nmcli -t -f NAME,TYPE connection show 2>/dev/null | while IFS=: read -r n t; do
    [ "$t" = "802-11-wireless" ] || [ "$t" = "wifi" ] || continue
    [ "$n" = "presence-hub-join" ] && continue
    nmcli connection modify "$n" connection.autoconnect no 2>/dev/null || true
  done
  echo WIFI_OK
else
  nmcli connection modify presence-hub-join connection.autoconnect no 2>/dev/null || true
  [ -z "$prev" ] || nmcli -w 15 connection up "$prev"
  echo WIFI_FAILED
fi
rm -f "$0"
SWITCH
sudo systemd-run --unit=presence-hub-join-switch --collect bash "$sw"
""".strip()


def _entry_forms(entry: str) -> set[str]:
    """名簿での書かれ方の揺れ(`.local` の有無)を含めた候補。"""
    short = entry[: -len(".local")] if entry.endswith(".local") else entry
    return {entry, short, f"{short}.local"}


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
    if not ssid or not psk:
        return StepResult(
            ok=False,
            message=(
                "このハブの AP 名またはパスワードが読めません。"
                ".kit/ap-join.env を確認してください"
            ),
        )
    # 書く前に検証する。値そのものはメッセージに出さない。
    if not psk_valid(psk):
        return StepResult(
            ok=False,
            message=(
                "このハブの AP パスワードは子Pi が使えない形式です"
                "(8〜63文字の半角英数記号、または16進64文字)。.kit/ap-join.env を確認してください"
            ),
        )
    if not 1 <= len(ssid.encode("utf-8")) <= 32 or "\x00" in ssid:
        return StepResult(ok=False, message="このハブの AP 名の長さが不正です(1〜32バイト)")

    # 揃える先が決まらないまま Wi-Fi を切り替えると、子は記録の届かない状態になる。先に確かめる。
    try:
        gw = load_ap_gw_ip(repo)
    except ValueError as e:
        return StepResult(ok=False, message=f"{e}。site.env を確認してください")

    mac_inner = (
        "cat /sys/class/net/wlan0/address 2>/dev/null || "
        "cat /sys/class/net/wlan1/address 2>/dev/null"
    )
    mac_out = ssh_pi(old_host, _nested_ssh(entry, mac_inner), runner=runner)
    mac = _extract_mac(mac_out)
    if not mac:
        return StepResult(
            ok=False,
            message=(
                f"{entry} の MAC が取れませんでした。"
                "旧親からその子へ SSH できるか確認してください"
            ),
            output=mac_out,
        )

    key_inner = (
        "umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; "
        f"grep -qxF {shlex.quote(pubkey)} ~/.ssh/authorized_keys || "
        f"printf '%s\\n' {shlex.quote(pubkey)} >> ~/.ssh/authorized_keys; "
        f"grep -qxF {shlex.quote(pubkey)} ~/.ssh/authorized_keys && echo {KEY_OK}"
    )
    key_out = ssh_pi(old_host, _nested_ssh(entry, key_inner), runner=runner)
    if KEY_OK not in (key_out or ""):
        return StepResult(
            ok=False,
            message=(
                f"{entry} に新しい公開鍵を入れられませんでした。"
                "Wi-Fi は切り替えていません。"
            ),
            output=key_out,
        )

    wifi_out = ssh_pi(
        old_host,
        _nested_ssh(entry, WIFI_INSTALL),
        runner=runner,
        input_text=nm_join_keyfile(ssid, psk),
    )
    # 切替が成功すると旧親への SSH が切れる。WIFI_OK が届かないのは正常。
    # 成功の判定は、このハブの AP に同じ MAC が現れること。
    waited = wait_fn(mac)
    if not waited.ok:
        return StepResult(
            ok=False,
            message=(
                f"{entry} がこのハブの AP ({ssid}) に現れません。"
                " 無線切替の返事が途中で切れるのは正常です。"
                " パスワードが違うか、電波の外の可能性があります。"
                " このハブの AP に居るなら、ウィザードで 1 → 3 を選んでください。"
                f" {waited.message}"
            ),
            output=(wifi_out or "") + (waited.output or ""),
        )

    inv_name = inventory_name(entry)
    kh = register_host_key(inv_name, runner=runner, known_hosts=known_hosts)
    ip = (waited.output or "").strip()
    if not (kh.output or "").strip() and _IPV4_RE.match(ip):
        kh = register_host_key(ip, runner=runner, known_hosts=known_hosts)
    if not kh.ok:
        return kh
    added = add_to_inventory(inv_name, path=inv_file)
    if not added.ok:
        return added

    # 子はもうこのハブの AP にいる。旧親経由ではなく、このハブから直接 SSH して
    # 記録の送り先(JSON の host と MQTT_HOST)を付け替え先の値へ揃える(設計 §2.4)。
    if _IPV4_RE.match(ip) and not ip_in_subnet(ip, gw, 24):
        return StepResult(
            ok=False,
            message=(
                f"{entry} の IP {ip} がこのハブの AP の範囲({gw}/24)にありません。"
                " site.env の AP_GW_IP と実際の AP が食い違っています。"
                " bash scripts/bootstrap-hub.sh 50 をやり直してください"
            ),
            output=waited.output,
        )
    aligned = align_send_target(ip if _IPV4_RE.match(ip) else inv_name, gw, runner=runner)
    if not aligned.ok:
        return StepResult(
            ok=False,
            message=(
                f"{entry} はこのハブへ移りましたが、記録の送り先を {gw} に揃えられませんでした。"
                " このままでは記録が届きません。"
                f" python3 -m fleet_ui.child_cli align {inv_name} を実行してください。"
                f" {aligned.message}"
            ),
            output=aligned.output,
        )

    remote_text, status = _read_remote_inventory(
        old_host, remote_inventory, runner=runner
    )
    head = f"{entry} をこのハブへ移しました（ホスト名と局番号はそのまま）。"
    where = f"旧親({old_host})"

    def done(*lines: str) -> StepResult:
        return StepResult(ok=True, message="\n".join((head, *lines)), output=waited.output)

    if status == "missing":
        return done(
            f"{where}には名簿 fleet/children.conf がありませんでした。旧親の名簿は変えていません。"
        )
    if status != "ok" or remote_text is None:
        return done(
            f"要対応: {where}の名簿を読めなかったので、{entry} の行は旧親に残っています。"
            "旧親で手動削除してください。"
        )
    before = parse_children_conf(remote_text)
    if entry not in before:
        other = sorted((_entry_forms(entry) - {entry}) & set(before))
        if other:
            return done(
                f"要対応: {where}の名簿には {entry} ではなく {other[0]} として載っているので、"
                "外していません。旧親で手動削除してください。"
            )
        return done(
            f"{where}の名簿には {entry} が載っていませんでした。旧親の名簿は変えていません。"
        )

    stripped = strip_inventory_entry(remote_text, entry)
    write_remote = f"printf %s {shlex.quote(stripped)} > {remote_inventory}"
    ssh_pi(old_host, write_remote, runner=runner)  # 返事は判定に使わない(失敗しても空文字)

    # 書いた側の申告ではなく、読み直した名簿で成否を決める。
    after_text, after_status = _read_remote_inventory(
        old_host, remote_inventory, runner=runner
    )
    if after_status != "ok" or after_text is None:
        return done(
            f"要対応: {where}の名簿を書き換えましたが、読み直せなかったので {entry} が外れたかは"
            "確かめられていません。旧親で fleet/children.conf を開き、"
            f"{entry} の行が残っていれば手動削除してください。"
        )
    after = parse_children_conf(after_text)
    if entry in after:
        return done(
            f"要対応: {where}の名簿から {entry} を外せませんでした（読み直すと行が残っています）。"
            "旧親で手動削除してください。"
        )
    if after != parse_children_conf(stripped):
        return done(
            f"要対応: {where}の名簿が想定と違う形になりました（{entry} 以外の行も変わっています）。"
            "旧親で git diff fleet/children.conf を確認して直してください。"
        )
    return done(
        f"{where}の名簿 fleet/children.conf から {entry} を外しました（読み直して確認済み）。",
        "このハブと旧親の fleet/children.conf が変わっています。"
        "commit してから deploy-parent.sh を実行してください。",
        "試しに移しただけなら、元のハブで 1 → 1 を選び、"
        "旧親にこのハブを指定して逆向きに移すと名簿も戻ります。",
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
