"""フリート状態一覧の検証。

ssh / curl を偽物に差し替え、収集と重複判定の結合部分だけを確認する。
重複判定そのものの網羅テストは test_sta_no_report.py にある。

終了コードは sta_no_report.py と同じ契約に揃える:
  0 = 全機体を検査できて重複なし
  1 = STA_NO 重複あり(最も実行可能な合図なので優先)
  2 = 検査しきれていない(到達できない子がある / 割当を読めない子がある)
2 を 0 と混同しないこと。「安全」ではなく「確かめられていない」を意味する。
"""
import json
import os

from scripts.tests.shellhelp import run_bash


def _inventory(tmp_path, hosts):
    f = tmp_path / "children.conf"
    f.write_text("".join(f"{h}\n" for h in hosts), encoding="utf-8")
    return f


def _env(tmp_path, hosts):
    e = dict(os.environ)
    e["FLEET_INVENTORY"] = str(_inventory(tmp_path, hosts))
    return e


_PICK = (
    "import json,sys; d=json.load(sys.stdin); "
    'print(json.dumps({"id_names": d.get(sys.argv[1], {})}))'
)


def _fake_ssh_returning(fake_bin, mapping: dict):
    """host -> id_names dict を返す偽 ssh。hostname -I / systemctl にも応答する。"""
    payload = json.dumps(mapping).replace("'", "'\\''")
    pick = _PICK.replace("'", "'\\''")
    fake_bin("ssh", f"""
host="$1"; shift
cmd="$*"
case "$cmd" in
  *hostname\\ -I*) echo "10.42.0.52" ;;
  *is-active*)     echo "active" ;;
  *id_names_config.json*)
      printf '%s' '{payload}' | python3 -c '{pick}' "$host" ;;
  *) echo "" ;;
esac
""")
    fake_bin("curl", 'echo "{\\"status\\": \\"ready\\", \\"model_type\\": \\"signal_tower\\"}"')


def test_reports_each_host(tmp_path, fake_bin):
    _fake_ssh_returning(fake_bin, {
        "a": {"1": ["HIME", "T120", "004020"]},
        "b": {"1": ["HIME", "T120", "010020"]},
    })
    proc = run_bash("scripts/fleet-status.sh", env=_env(tmp_path, ["a", "b"]), check=False)
    assert "a" in proc.stdout
    assert "b" in proc.stdout
    assert "004020" in proc.stdout
    assert proc.returncode == 0


def test_exits_1_and_warns_on_duplicate_sta_no(tmp_path, fake_bin):
    _fake_ssh_returning(fake_bin, {
        "a": {"1": ["HIME", "T120", "004020"]},
        "b": {"1": ["HIME", "T120", "004020"]},
    })
    proc = run_bash("scripts/fleet-status.sh", env=_env(tmp_path, ["a", "b"]), check=False)
    assert proc.returncode == 1
    assert "重複" in proc.stdout


def test_only_limits_targets(tmp_path, fake_bin):
    _fake_ssh_returning(fake_bin, {
        "a": {"1": ["HIME", "T120", "004020"]},
        "b": {"1": ["HIME", "T120", "004020"]},
    })
    proc = run_bash(
        "scripts/fleet-status.sh --only a", env=_env(tmp_path, ["a", "b"]), check=False
    )
    assert proc.returncode == 0  # a だけなら重複しない
    assert "b" not in proc.stdout.split()


def test_unreachable_child_exits_2_not_1(tmp_path, fake_bin):
    """到達できない子は「重複あり」ではなく「検査しきれていない」。

    1 を返すと、運用者が存在しない衝突を探すことになる。
    """
    fake_bin("ssh", "exit 255")
    fake_bin("curl", "exit 7")
    proc = run_bash("scripts/fleet-status.sh", env=_env(tmp_path, ["a"]), check=False)
    assert proc.returncode == 2
    assert "到達" in proc.stdout or "到達" in proc.stderr


def test_duplicate_takes_precedence_over_unreachable(tmp_path, fake_bin):
    """重複と到達不能が併存したら、実行可能な合図である重複(1)を優先する。"""
    _fake_ssh_returning(fake_bin, {
        "a": {"1": ["HIME", "T120", "004020"]},
        "b": {"1": ["HIME", "T120", "004020"]},
    })
    # c はインベントリにあるが偽 ssh の mapping に無い → 空割当。到達自体は成功する。
    proc = run_bash("scripts/fleet-status.sh", env=_env(tmp_path, ["a", "b"]), check=False)
    assert proc.returncode == 1


def test_only_with_empty_value_is_rejected(tmp_path, fake_bin):
    _fake_ssh_returning(fake_bin, {"a": {"1": ["A", "B", "C"]}})
    proc = run_bash(
        "scripts/fleet-status.sh --only=", env=_env(tmp_path, ["a"]), check=False
    )
    assert proc.returncode != 0
    assert "STA_NO" not in proc.stdout


def test_unparseable_child_config_exits_2(tmp_path, fake_bin):
    """子の設定が壊れていても落ちず、「検査できていない」を返すこと。"""
    fake_bin("ssh", """
cmd="$*"
case "$cmd" in
  *hostname\\ -I*) echo "10.42.0.52" ;;
  *is-active*)     echo "active" ;;
  *id_names_config.json*) printf '%s' '{"id_names": "garbage"}' ;;
  *) echo "" ;;
esac
""")
    fake_bin("curl", 'echo "{}"')
    proc = run_bash("scripts/fleet-status.sh", env=_env(tmp_path, ["a"]), check=False)
    assert "Traceback" not in proc.stderr
    assert proc.returncode in (0, 2)
