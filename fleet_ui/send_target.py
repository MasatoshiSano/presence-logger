"""子の記録の送り先を、付け替え先ハブの AP_GW_IP に揃える。

子の記録送信は2経路あり、送り先の読み元が違う。

- カメラ内蔵の自動送信: ~/send_target_config.json の host(送信のたびに読み直す)
- child-csv-to-mqtt.py(ACK 付き): 環境変数 MQTT_HOST(既定 10.42.0.1)。JSON は読まない

片方だけ変えると、もう片方が旧 IP へ送り続けて記録が黙って届かなくなる。子のコード(child/)は
変えない方針なので、MQTT_HOST は systemd の drop-in で渡す。付け替えのたびに「常に」付け替え先の
値へ揃える(既定のときは drop-in を消す)。設計: docs/2026-09-29-child-join-fix-design.md §2.4
"""
from __future__ import annotations

import ipaddress
import re
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fleet_ui.provision import StepResult

DEFAULT_GW = "10.42.0.1"
DROPIN = "/etc/systemd/system/child-csv-to-mqtt.service.d/10-hub-gw.conf"
JOIN_PROFILE = "/etc/NetworkManager/system-connections/presence-hub-join.nmconnection"
SERVICE = "child-csv-to-mqtt"

REPO_ROOT = Path(__file__).resolve().parents[1]

# ssh の argv と remote 文字列へ入る値なので厳しくする。
_SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_SAFE_PATH_RE = re.compile(r"^/[A-Za-z0-9._/+-]+\Z")
_MQTT_HOST_RE = re.compile(r'(?:^|[\s"=])MQTT_HOST=([^\s"]*)')
_READBACK_KEYS = ("HOST_NOW", "ENV_NOW", "ACTIVE_NOW", "JOIN_PROFILE", "ALIGN_ERROR")


def _check_ipv4(value: str) -> str:
    """厳密な IPv4(先頭ゼロ・改行・CIDR なし)。外れたら ValueError。"""
    try:
        ok = str(ipaddress.IPv4Address(value)) == value
    except ValueError:
        ok = False
    if not ok:
        raise ValueError(f"IPv4 アドレスではありません: {value!r}")
    return value


def ip_in_subnet(ip: str, gw: str, prefix: int = 24) -> bool:
    """ip が gw/prefix の範囲に入っているか。どちらかが IPv4 でなければ False。"""
    try:
        net = ipaddress.ip_network(f"{gw}/{prefix}", strict=False)
        return ipaddress.ip_address(ip) in net
    except ValueError:
        return False


def load_ap_gw_ip(repo: Path | None = None) -> str:
    """ハブの site.env の AP_GW_IP。無ければ既定。IPv4 でなければ ValueError。"""
    path = (repo or REPO_ROOT) / "site.env"
    if not path.is_file():
        return DEFAULT_GW
    value = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("AP_GW_IP="):
            value = line[len("AP_GW_IP="):].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
    if not value:
        return DEFAULT_GW
    try:
        return _check_ipv4(value)
    except ValueError:
        raise ValueError(f"site.env の AP_GW_IP が IPv4 ではありません: {value!r}") from None


# 子の上で走る。JSON は host だけ差し替える(一時ファイル→os.replace、モード・所有者を保つ)。
_PY_ALIGN_JSON = """
import json, os, sys, tempfile
p, gw = sys.argv[1], sys.argv[2]
try:
    st = os.stat(p)
except FileNotFoundError:
    cfg, mode, uid, gid = {}, 0o644, os.getuid(), os.getgid()
else:
    try:
        with open(p, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except ValueError:
        sys.exit(3)
    if not isinstance(cfg, dict):
        sys.exit(3)
    mode, uid, gid = st.st_mode & 0o777, st.st_uid, st.st_gid
cfg["host"] = gw
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p))
try:
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\\n")
    os.chmod(tmp, mode)
    os.chown(tmp, uid, gid)
    os.replace(tmp, p)
except BaseException:
    if os.path.exists(tmp):
        os.unlink(tmp)
    raise
""".strip()

_PY_READ_HOST = (
    "import json,sys\n"
    "try:\n"
    "    print(json.load(open(sys.argv[1], encoding='utf-8')).get('host', ''))\n"
    "except Exception:\n"
    "    print('')"
)


def align_remote_script(
    gw: str, *, dropin: str = DROPIN, join_profile: str = JOIN_PROFILE
) -> str:
    """子の上で走る bash。gw は IPv4 でなければ ValueError(shlex.quote もする)。

    1) ~/send_target_config.json の host だけ置き換える(壊れた JSON なら何もせず exit 3)
    2) gw が既定なら drop-in を消し、違えば MQTT_HOST の drop-in を置く
    3) daemon-reload と restart
    4) 読み手側で確認して出力: HOST_NOW / ENV_NOW / ACTIVE_NOW / JOIN_PROFILE
       (JOIN_PROFILE は presence-hub-join が引用符つきか。§1.5。報告だけで直さない。
        grep -q なので PSK は出力に出ない)
    """
    _check_ipv4(gw)
    for p in (dropin, join_profile):
        if not _SAFE_PATH_RE.match(p):
            raise ValueError(f"パスに使えない文字があります: {p!r}")
    q = shlex.quote
    if gw == DEFAULT_GW:
        dropin_step = f"sudo rm -f {q(dropin)}"
    else:
        dropin_step = (
            'dtmp=$(mktemp) && '
            f"printf '[Service]\\nEnvironment=MQTT_HOST=%s\\n' {q(gw)} > \"$dtmp\" && "
            f'sudo install -D -m 644 "$dtmp" {q(dropin)} && rm -f "$dtmp"'
        )
    return f"""set -u
f="$HOME/send_target_config.json"
python3 - "$f" {q(gw)} <<'PY'
{_PY_ALIGN_JSON}
PY
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "ALIGN_ERROR=send_target_config.json を書き換えられません(壊れているか読めません)"
  exit 3
fi
{dropin_step} || {{ echo "ALIGN_ERROR=drop-in を更新できません"; exit 4; }}
sudo systemctl daemon-reload
sudo systemctl restart {SERVICE}
echo "HOST_NOW=$(python3 -c {q(_PY_READ_HOST)} "$f")"
echo "ENV_NOW=$(systemctl show -p Environment {SERVICE} 2>/dev/null | sed 's/^Environment=//')"
echo "ACTIVE_NOW=$(systemctl is-active {SERVICE} 2>/dev/null || true)"
if sudo test -f {q(join_profile)}; then
  if sudo grep -qE '^(ssid|psk)=".*"$' {q(join_profile)}; then
    echo JOIN_PROFILE=quoted
  else
    echo JOIN_PROFILE=ok
  fi
else
  echo JOIN_PROFILE=absent
fi
"""


def parse_align_output(text: str) -> dict[str, str]:
    """align_remote_script の出力から読み戻しの値を取り出す。無い項目は空文字。"""
    out = {k.lower(): "" for k in _READBACK_KEYS}
    for line in (text or "").splitlines():
        key, sep, val = line.partition("=")
        if sep and key in _READBACK_KEYS:
            out[key.lower()] = val.strip()
    return out


def _mqtt_host_from_env(env_now: str) -> str | None:
    found = _MQTT_HOST_RE.findall(env_now or "")
    return found[-1] if found else None  # systemd は後ろの指定が勝つ


def align_send_target(
    host: str, gw: str, *, runner: Callable[..., str]
) -> StepResult:
    """host(IP かホスト名)の子を SSH で gw に揃える。

    書いた側の終了コードではなく、子の上で読み直した HOST_NOW / ENV_NOW / ACTIVE_NOW で判定する。
    ok = HOST_NOW==gw かつ (gw==既定 なら MQTT_HOST が無いか既定; 非既定なら MQTT_HOST==gw)
         かつ ACTIVE_NOW==active。詳細(JOIN_PROFILE を含む)は output に生のまま残す。
    """
    from fleet_ui.provision import StepResult  # provision もこのモジュールを使うので遅延

    _check_ipv4(gw)
    if not _SAFE_HOST_RE.match(host or ""):
        return StepResult(ok=False, message=f"子の宛先が不正です: {host!r}")
    remote = f"bash -c {shlex.quote(align_remote_script(gw))}"
    cmd = [
        "ssh",
        "-o", "ConnectTimeout=8",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        f"pi@{host}",
        remote,
    ]
    try:
        out = runner(cmd, input_text=None) or ""
    except TypeError:
        out = runner(cmd) or ""

    r = parse_align_output(out)
    problems: list[str] = []
    if not r["host_now"]:
        detail = r["align_error"] or "子の上で読み戻せませんでした(SSH できないか、途中で失敗)"
        problems.append(detail)
    elif r["host_now"] != gw:
        problems.append(f"send_target_config.json の host が {r['host_now']} のままです(期待 {gw})")
    if r["host_now"]:
        mqtt = _mqtt_host_from_env(r["env_now"])
        if gw == DEFAULT_GW:
            if mqtt not in (None, DEFAULT_GW):
                problems.append(f"MQTT_HOST={mqtt} が残っています(既定 {DEFAULT_GW} に戻る想定)")
        elif mqtt != gw:
            problems.append(f"MQTT_HOST が {gw} になっていません(実際: {mqtt or '未設定'})")
        if r["active_now"] != "active":
            problems.append(f"{SERVICE} が動いていません({r['active_now'] or '状態不明'})")
    if problems:
        return StepResult(ok=False, message="; ".join(problems), output=out)
    return StepResult(ok=True, message=f"記録の送り先を {gw} に揃えました", output=out)
