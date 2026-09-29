"""NetworkManager 接続ファイルを「読み手」で読み戻すテスト専用ヘルパ(本番からは import しない)。

書いた側の文字列を眺める(`'ssid="x"' in body`)テストは、引用符が値の一部になる不具合
(2026-09-25)を通してしまった。ここでは独立した読み手 2 つで読み戻す:

- NetworkManager 自身: `nmcli --offline connection modify`(標準入力を読み標準出力へ書くだけ。
  システムの接続は変えない)。元の値を `connection add` に直接渡した出力と比べる。
- GLib.KeyFile: NM の keyfile の土台。venv に gi が無いので /usr/bin/python3 をサブプロセスで使う。

どちらかが使えなければ pytest.skip(理由つき)。片方が skip しても、もう片方は独立に走る。
skip が常態化すると検出力が落ちるので、Pi 上で `pytest -rs` で skip 件数を見て走らせること。
"""
import json
import re
import shutil
import subprocess
import textwrap

import pytest

_GLIB_SCRIPT = textwrap.dedent(
    """
    import json, sys
    import gi
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib
    kf = GLib.KeyFile()
    kf.load_from_file(sys.argv[1], GLib.KeyFileFlags.NONE)
    out = {"ssid": kf.get_string("wifi", "ssid"), "psk": kf.get_string("wifi-security", "psk")}
    print(json.dumps(out))
    """
)
_BYTES_FORM = re.compile(r"^(?:\d{1,3};)+$")


def _nmcli(args, stdin=None):
    exe = shutil.which("nmcli")
    if not exe:
        pytest.skip("nmcli が無い")
    proc = subprocess.run(  # noqa: S603 (fixed argv, no shell)
        [exe, "--offline", *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    if "nrecognized option" in proc.stderr or "nknown option" in proc.stderr:
        pytest.skip("nmcli が --offline に対応していない")
    return proc


def _pick(text: str) -> dict:
    """keyfile 出力から ssid / psk / autoconnect-retries の行を辞書にする。"""
    out: dict = {}
    section = ""
    for line in text.splitlines():
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == "wifi" and line.startswith("ssid="):
            out["ssid"] = line[len("ssid="):]
        elif section == "wifi-security" and line.startswith("psk="):
            out["psk"] = line[len("psk="):]
        elif section == "connection" and line.startswith("autoconnect-retries="):
            out["autoconnect-retries"] = line[len("autoconnect-retries="):]
    return out


def nm_read(path) -> dict:
    """NM 自身の読み手: ファイルを読ませ、正規化して書き戻された行を返す。

    NM が読めない(SSID が消える等)場合は skip でなく失敗にする。"""
    proc = _nmcli(
        ["connection", "modify", "connection.autoconnect-priority", "200"],
        stdin=open(path, encoding="utf-8").read(),  # noqa: SIM115
    )
    assert proc.returncode == 0, f"NM がこのファイルを読めない: {proc.stderr.strip()}"
    return _pick(proc.stdout)


def nm_canonical(ssid: str, psk: str) -> dict:
    """同じ nmcli に元の値を直接渡した(add)ときの ssid / psk の行。"""
    proc = _nmcli([
        "connection", "add", "type", "wifi", "con-name", "t", "ifname", "*",
        "ssid", ssid, "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", psk,
    ])
    assert proc.returncode == 0, f"nmcli add 失敗: {proc.stderr.strip()}"
    return _pick(proc.stdout)


def glib_read(path) -> dict:
    """GLib.KeyFile で get_string した値。"""
    if not shutil.which("/usr/bin/python3"):
        pytest.skip("/usr/bin/python3 が無い")
    proc = subprocess.run(  # noqa: S603 (fixed argv, no shell)
        ["/usr/bin/python3", "-c", _GLIB_SCRIPT, str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if "No module named 'gi'" in proc.stderr:
        pytest.skip("gi(GLib) が無い")
    assert proc.returncode == 0, f"GLib が読めない: {proc.stderr.strip()}"
    return json.loads(proc.stdout)


def glib_ssid_bytes(raw: str) -> bytes:
    """GLib が返した ssid 文字列を、実際の SSID バイト列へ戻す(NM の 2 形式)。"""
    if _BYTES_FORM.match(raw):
        return bytes(int(x) for x in raw.split(";") if x)
    return raw.replace("\\;", ";").encode("utf-8")


def assert_glib_roundtrip(path, ssid: str, psk: str) -> None:
    g = glib_read(path)
    assert g["psk"] == psk, "GLib 層で PSK が元の値と違う"
    assert glib_ssid_bytes(g["ssid"]) == ssid.encode("utf-8"), "GLib 層で SSID が元の値と違う"


def assert_nm_roundtrip(path, ssid: str, psk: str) -> None:
    got = nm_read(path)
    want = nm_canonical(ssid, psk)
    assert got["ssid"] == want["ssid"], "NM が読んだ SSID が元の値と違う"
    assert got["psk"] == want["psk"], "NM が読んだ PSK が元の値と違う"


def assert_roundtrip(path, ssid: str, psk: str) -> None:
    """NM と GLib の両方が、書いた値を元の SSID / PSK として読むこと。

    片方が skip しても、もう片方の失敗は隠れない(先に走らせた方が落ちればそこで止まる)。"""
    skipped = []
    for check in (assert_nm_roundtrip, assert_glib_roundtrip):
        try:
            check(path, ssid, psk)
        except pytest.skip.Exception as e:
            skipped.append(str(e))
    if len(skipped) == 2:
        pytest.skip("; ".join(skipped))
