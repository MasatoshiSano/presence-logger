"""子の記録の送り先(JSON の host と MQTT_HOST の drop-in)を揃える処理の検証。

設計: docs/2026-09-29-child-join-fix-design.md §2.4。
子の上で走る bash は、偽の sudo / systemctl を PATH の先頭に置いて一時 HOME で実行する。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from fleet_ui.provision import StepResult
from fleet_ui.send_target import (
    DEFAULT_GW,
    DROPIN,
    align_remote_script,
    align_send_target,
    load_ap_gw_ip,
    parse_align_output,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# --- B2-T1 load_ap_gw_ip ----------------------------------------------------


def _site_env(tmp_path, text):
    (tmp_path / "site.env").write_text(text, encoding="utf-8")
    return tmp_path


def test_load_ap_gw_ip_reads_site_env(tmp_path):
    repo = _site_env(tmp_path, "AP_SSID=x\nAP_GW_IP=10.42.1.1\n")
    assert load_ap_gw_ip(repo) == "10.42.1.1"


def test_load_ap_gw_ip_defaults_when_missing(tmp_path):
    assert load_ap_gw_ip(tmp_path) == DEFAULT_GW  # site.env 自体が無い
    assert load_ap_gw_ip(_site_env(tmp_path, "AP_SSID=x\n")) == DEFAULT_GW
    assert load_ap_gw_ip(_site_env(tmp_path, "AP_GW_IP=\n")) == DEFAULT_GW


def test_load_ap_gw_ip_accepts_quoted_value(tmp_path):
    repo = _site_env(tmp_path, 'AP_GW_IP="10.42.2.1"\n')
    assert load_ap_gw_ip(repo) == "10.42.2.1"


@pytest.mark.parametrize(
    "bad",
    ["10.42.1", "10.42.1.256", "10.42.01.1", "10.42.1.1; rm", "abc", "10.42.1.1/24", "::1"],
)
def test_load_ap_gw_ip_rejects_non_ipv4(tmp_path, bad):
    repo = _site_env(tmp_path, f"AP_GW_IP={bad}\n")
    with pytest.raises(ValueError, match="AP_GW_IP"):
        load_ap_gw_ip(repo)


# --- B2-T2..T5 子の上で走る bash ---------------------------------------------


_FAKE_SYSTEMCTL = r"""#!/bin/bash
# 偽の systemd。MainPID と「実プロセスの環境」を持つ。プロセスは restart 時点の drop-in を
# 起動時の環境として受け取る(=読み込んだ設定と実プロセスの環境は別物)。
# 操作つまみ: FAKE_RESTART_RC(非0で restart 失敗・旧プロセス残留), FAKE_PID_SAME=1(PID 不変),
#            FAKE_PROC_MQTT(実プロセスの MQTT_HOST を強制), FAKE_ACTIVE
echo "systemctl $*" >> "$FAKE_LOG"
base="$(dirname "$FAKE_LOG")"
state="$base/state"
proc="$base/proc"
mkdir -p "$state"
[ -f "$state/pid" ] || echo "${FAKE_PID_BEFORE:-100}" > "$state/pid"
dropin_mqtt() {
  [ -f "$FAKE_DROPIN" ] || return 0
  grep -h '^Environment=MQTT_HOST=' "$FAKE_DROPIN" | tail -n 1 | cut -d= -f3-
}
case "$1" in
  show)
    case "$*" in
      *MainPID*) cat "$state/pid" ;;
      *)
        if [ -f "$FAKE_DROPIN" ]; then
          echo "Environment=$(grep -h ^Environment= "$FAKE_DROPIN" | cut -d= -f2-)"
        else echo Environment=; fi ;;
    esac ;;
  is-active) echo "${FAKE_ACTIVE:-active}" ;;
  restart)
    if [ "${FAKE_RESTART_RC:-0}" -ne 0 ]; then exit "$FAKE_RESTART_RC"; fi
    if [ -z "${FAKE_PID_SAME:-}" ]; then
      new="${FAKE_PID_AFTER:-200}"
      echo "$new" > "$state/pid"
      mkdir -p "$proc/$new"
      host="${FAKE_PROC_MQTT:-$(dropin_mqtt)}"
      { printf 'PATH=/usr/bin\0'; [ -n "$host" ] && printf 'MQTT_HOST=%s\0' "$host"; } \
        > "$proc/$new/environ"
    fi ;;
esac
exit 0
"""


def _fake_child(tmp_path):
    """偽の sudo(そのまま実行)・systemctl(上記)・sleep(待たない)。"""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    dropin = tmp_path / "etc" / "10-hub-gw.conf"
    log = tmp_path / "calls.log"
    old_proc = tmp_path / "proc" / "100"  # restart 前の旧プロセス(旧 GW の環境)
    old_proc.mkdir(parents=True)
    (old_proc / "environ").write_bytes(b"PATH=/usr/bin\0MQTT_HOST=10.42.0.1\0")
    (bindir / "sudo").write_text('#!/bin/bash\nexec "$@"\n', encoding="utf-8")
    (bindir / "sleep").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    (bindir / "systemctl").write_text(_FAKE_SYSTEMCTL, encoding="utf-8")
    for f in ("sudo", "sleep", "systemctl"):
        (bindir / f).chmod(0o755)
    return home, bindir, dropin, log


def _run_align(
    tmp_path, gw, *, cfg_text=None, mode=0o600, dropin_text=None, active="active", fake_env=None
):
    home, bindir, dropin, log = _fake_child(tmp_path)
    cfg = home / "send_target_config.json"
    if cfg_text is not None:
        cfg.write_text(cfg_text, encoding="utf-8")
        cfg.chmod(mode)
    if dropin_text is not None:
        dropin.parent.mkdir(parents=True, exist_ok=True)
        dropin.write_text(dropin_text, encoding="utf-8")
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "FAKE_LOG": str(log),
        "FAKE_DROPIN": str(dropin),
        "FAKE_ACTIVE": active,
        **(fake_env or {}),
    }
    script = align_remote_script(gw, dropin=str(dropin), proc_root=str(tmp_path / "proc"))
    proc = subprocess.run(  # noqa: S603
        ["bash", "-c", script],  # noqa: S607
        env=env, capture_output=True, text=True, check=False,
    )
    return proc, cfg, dropin, log


def test_align_script_changes_only_host_and_keeps_mode(tmp_path):  # B2-T2
    original = {"host": "10.42.0.1", "password": "dummy-pass", "enabled": False, "port": 1883}
    proc, cfg, _dropin, _log = _run_align(
        tmp_path, "10.42.1.1", cfg_text=json.dumps(original), mode=0o640
    )
    assert proc.returncode == 0, proc.stderr
    got = json.loads(cfg.read_text(encoding="utf-8"))
    assert got == {**original, "host": "10.42.1.1"}
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o640
    assert "HOST_NOW=10.42.1.1" in proc.stdout


def test_align_script_default_gw_removes_the_dropin(tmp_path):  # B2-T3
    proc, cfg, dropin, log = _run_align(
        tmp_path, DEFAULT_GW,
        cfg_text='{"host": "10.42.1.1"}',
        dropin_text="[Service]\nEnvironment=MQTT_HOST=10.42.1.1\n",
    )
    assert proc.returncode == 0, proc.stderr
    assert not dropin.exists()
    assert json.loads(cfg.read_text(encoding="utf-8"))["host"] == DEFAULT_GW
    calls = log.read_text(encoding="utf-8")
    assert "daemon-reload" in calls
    assert "restart child-csv-to-mqtt" in calls


def test_align_script_non_default_writes_dropin(tmp_path):  # B2-T4
    proc, _cfg, dropin, log = _run_align(tmp_path, "10.42.1.1", cfg_text='{"host": "10.42.0.1"}')
    assert proc.returncode == 0, proc.stderr
    assert dropin.read_text(encoding="utf-8") == "[Service]\nEnvironment=MQTT_HOST=10.42.1.1\n"
    assert stat.S_IMODE(dropin.stat().st_mode) == 0o644
    # 読み戻し側の出力が drop-in の効果を映している
    assert "MQTT_HOST=10.42.1.1" in parse_align_output(proc.stdout)["env_now"]
    calls = log.read_text(encoding="utf-8")
    assert calls.index("daemon-reload") < calls.index("restart child-csv-to-mqtt")


def test_align_script_broken_json_is_not_overwritten(tmp_path):  # B2-T5
    proc, cfg, dropin, log = _run_align(tmp_path, "10.42.1.1", cfg_text="{not json")
    assert proc.returncode != 0
    assert cfg.read_text(encoding="utf-8") == "{not json"
    assert not dropin.exists()  # JSON を直せないまま片方だけ変えない
    assert not log.exists()  # systemctl にも触らない


def test_align_script_creates_json_with_only_host_when_missing(tmp_path):
    proc, cfg, _dropin, _log = _run_align(tmp_path, "10.42.1.1")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(cfg.read_text(encoding="utf-8")) == {"host": "10.42.1.1"}


def test_align_script_rejects_json_that_is_not_an_object(tmp_path):
    proc, cfg, _dropin, _log = _run_align(tmp_path, "10.42.1.1", cfg_text="[1, 2]")
    assert proc.returncode != 0
    assert cfg.read_text(encoding="utf-8") == "[1, 2]"


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ('ssid="quoted-hub"\npsk=abcdefgh\n', "quoted"),
        ('ssid=plain-hub\npsk="abcdefgh"\n', "quoted"),
        ("ssid=plain-hub\npsk=abcdefgh\n", "ok"),
        (None, "absent"),
    ],
)
def test_align_script_reports_quoted_join_profile_without_touching_it(
    tmp_path, monkeypatch, profile, expected
):
    """§1.5: 引用符つき presence-hub-join は報告だけ。PSK は出力に載せない。"""
    prof_dir = tmp_path / "nm"
    prof_dir.mkdir()
    path = prof_dir / "presence-hub-join.nmconnection"
    if profile is not None:
        path.write_text(profile, encoding="utf-8")
    script = align_remote_script("10.42.1.1", dropin=str(tmp_path / "d.conf"),
                                 join_profile=str(path), proc_root=str(tmp_path / "proc"))
    home, bindir, _dropin, log = _fake_child(tmp_path)
    proc = subprocess.run(  # noqa: S603
        ["bash", "-c", script],  # noqa: S607
        env={**os.environ, "HOME": str(home), "PATH": f"{bindir}:{os.environ['PATH']}",
             "FAKE_LOG": str(log), "FAKE_DROPIN": str(tmp_path / "d.conf")},
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert parse_align_output(proc.stdout)["join_profile"] == expected
    assert "abcdefgh" not in proc.stdout + proc.stderr
    if profile is not None:
        assert path.read_text(encoding="utf-8") == profile  # 直さない


def test_align_script_never_edits_network_manager_profiles():
    script = align_remote_script("10.42.1.1")
    assert "nmcli" not in script
    assert "sed -i" not in script
    assert "system-connections/presence-hub-join" in script  # 読むだけ(grep -q / test -f)
    assert "> /etc/NetworkManager" not in script
    assert "connection modify" not in script


def test_default_dropin_path_is_the_designed_one():
    assert DROPIN == "/etc/systemd/system/child-csv-to-mqtt.service.d/10-hub-gw.conf"
    assert DEFAULT_GW == "10.42.0.1"


# --- B2-T6 読み戻しで判定する -------------------------------------------------


def _readback(
    host="10.42.1.1", proc_mqtt="10.42.1.1", active="active", join="ok",
    pid_before="100", pid_now="200", proc_env="ok", env="Environment=MQTT_HOST=10.42.1.1",
):
    return (
        f"noise line\nPID_BEFORE={pid_before}\nPID_NOW={pid_now}\nPROC_ENV={proc_env}\n"
        f"PROC_MQTT_HOST={proc_mqtt}\nHOST_NOW={host}\nENV_NOW={env}\n"
        f"ACTIVE_NOW={active}\nJOIN_PROFILE={join}\n"
    )


def _runner_returning(text):
    calls = []

    def run(cmd, input_text=None):
        calls.append(cmd)
        return text

    run.calls = calls
    return run


def test_align_send_target_ok_when_readback_matches():
    run = _runner_returning(_readback())
    res = align_send_target("10.42.1.50", "10.42.1.1", runner=run)
    assert res.ok, res.message
    assert isinstance(res, StepResult)
    (cmd,) = run.calls
    assert cmd[0] == "ssh"
    assert "pi@10.42.1.50" in cmd
    assert "BatchMode=yes" in cmd
    assert "HOST_NOW=" in res.output  # child_cli が表にするため生の読み戻しを残す


def test_align_send_target_fails_when_host_readback_differs():
    res = align_send_target(
        "10.42.1.50", "10.42.1.1", runner=_runner_returning(_readback(host="10.42.0.1"))
    )
    assert not res.ok
    assert "10.42.0.1" in res.message


def test_align_send_target_fails_when_process_mqtt_host_wrong():
    for proc_mqtt in ("", "10.42.0.1", "10.42.1.10"):
        res = align_send_target(
            "10.42.1.50", "10.42.1.1", runner=_runner_returning(_readback(proc_mqtt=proc_mqtt))
        )
        assert not res.ok, proc_mqtt
        assert "MQTT_HOST" in res.message


def test_align_send_target_ignores_loaded_unit_environment():
    """systemctl show の Environment は読み込んだ設定。実プロセスが旧 IP なら成功にしない。"""
    res = align_send_target(
        "10.42.1.50", "10.42.1.1",
        runner=_runner_returning(
            _readback(proc_mqtt="10.42.0.1", env="Environment=MQTT_HOST=10.42.1.1")
        ),
    )
    assert not res.ok


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pid_now": "100"},  # 再起動されていない
        {"pid_now": "0"},
        {"pid_now": ""},
        {"pid_before": "", "pid_now": ""},
        {"proc_env": "unreadable"},
    ],
)
def test_align_send_target_fails_unless_a_new_process_environment_was_read(kwargs):
    res = align_send_target(
        "10.42.1.50", "10.42.1.1", runner=_runner_returning(_readback(**kwargs))
    )
    assert not res.ok


def test_align_send_target_fails_when_service_not_active():
    for active in ("inactive", "failed", "activating", ""):
        res = align_send_target(
            "10.42.1.50", "10.42.1.1", runner=_runner_returning(_readback(active=active))
        )
        assert not res.ok, active
        assert "child-csv-to-mqtt" in res.message


def test_align_send_target_fails_on_empty_output_even_if_ssh_returned():
    res = align_send_target("10.42.1.50", "10.42.1.1", runner=_runner_returning(""))
    assert not res.ok


def test_align_send_target_default_gw_needs_no_or_default_mqtt_host():
    for proc_mqtt in ("", "10.42.0.1"):
        res = align_send_target(
            "10.42.0.50", DEFAULT_GW,
            runner=_runner_returning(_readback(host=DEFAULT_GW, proc_mqtt=proc_mqtt)),
        )
        assert res.ok, (proc_mqtt, res.message)


def test_align_send_target_default_gw_fails_if_stale_non_default_env_remains():
    res = align_send_target(
        "10.42.0.50", DEFAULT_GW,
        runner=_runner_returning(_readback(host=DEFAULT_GW, proc_mqtt="10.42.1.1")),
    )
    assert not res.ok


# --- B2-T6b 再起動の確認は「読み込んだ設定」でなく「動いているプロセス」で -------------


def _align_end_to_end(tmp_path, gw, **run_kwargs):
    """偽の子の上で本物の remote script を走らせ、その stdout を align_send_target の判定に通す。"""
    proc, _cfg, _dropin, log = _run_align(
        tmp_path, gw, cfg_text='{"host": "10.42.0.1"}', **run_kwargs
    )
    res = align_send_target("10.42.1.50", gw, runner=_runner_returning(proc.stdout))
    return proc, res, log


def _run_script_with_systemctl(tmp_path, systemctl_body):
    home, bindir, dropin, _log = _fake_child(tmp_path)
    (bindir / "systemctl").write_text(systemctl_body, encoding="utf-8")
    script = align_remote_script("10.42.1.1", dropin=str(dropin), proc_root=str(tmp_path / "proc"))
    return subprocess.run(  # noqa: S603
        ["bash", "-c", script],  # noqa: S607
        env={**os.environ, "HOME": str(home), "PATH": f"{bindir}:{os.environ['PATH']}"},
        capture_output=True, text=True, check=False, timeout=30,
    )


def test_restart_failure_is_a_failure_even_if_show_and_is_active_look_fine(tmp_path):  # (a)
    proc, res, _log = _align_end_to_end(tmp_path, "10.42.1.1", fake_env={"FAKE_RESTART_RC": "1"})
    assert proc.returncode != 0
    assert "ALIGN_ERROR=" in proc.stdout
    assert "ACTIVE_NOW=" not in proc.stdout  # 失敗した時点で止まり、読み戻しで成功を装わない
    assert not res.ok
    assert "restart" in res.message


def test_daemon_reload_failure_is_a_failure(tmp_path):
    proc = _run_script_with_systemctl(
        tmp_path, '#!/bin/bash\n[ "$1" = daemon-reload ] && exit 1\nexit 0\n'
    )
    assert proc.returncode != 0
    assert "daemon-reload" in proc.stdout
    res = align_send_target("10.42.1.50", "10.42.1.1", runner=_runner_returning(proc.stdout))
    assert not res.ok


def test_unchanged_main_pid_is_a_failure(tmp_path):  # (b)
    proc, res, _log = _align_end_to_end(tmp_path, "10.42.1.1", fake_env={"FAKE_PID_SAME": "1"})
    assert "PID_BEFORE=100" in proc.stdout
    assert "PID_NOW=100" in proc.stdout  # 新しい PID が現れないまま待ちきった
    assert not res.ok
    assert "再起動" in res.message


def test_process_environment_with_old_gw_is_a_failure(tmp_path):  # (c)
    proc, res, _log = _align_end_to_end(
        tmp_path, "10.42.1.1", fake_env={"FAKE_PROC_MQTT": "10.42.0.1"}
    )
    # 読み込んだ設定(drop-in / ENV_NOW)は新 GW、実プロセスだけ旧 GW
    assert "ENV_NOW=MQTT_HOST=10.42.1.1" in proc.stdout
    assert "ACTIVE_NOW=active" in proc.stdout
    assert "PROC_MQTT_HOST=10.42.0.1" in proc.stdout
    assert not res.ok
    assert "MQTT_HOST" in res.message


def test_healthy_restart_succeeds_for_non_default_gw(tmp_path):  # (d)
    proc, res, _log = _align_end_to_end(tmp_path, "10.42.1.1")
    assert proc.returncode == 0, proc.stderr
    assert "PID_NOW=200" in proc.stdout
    assert "PROC_MQTT_HOST=10.42.1.1" in proc.stdout
    assert res.ok, res.message


def test_healthy_restart_succeeds_for_default_gw_and_drops_stale_env(tmp_path):  # (d)
    stale = "[Service]\nEnvironment=MQTT_HOST=10.42.1.1\n"
    proc, res, _log = _align_end_to_end(tmp_path, DEFAULT_GW, dropin_text=stale)
    assert proc.returncode == 0, proc.stderr
    assert "PROC_MQTT_HOST=\n" in proc.stdout  # drop-in を消したので実プロセスに MQTT_HOST は無い
    assert res.ok, res.message


def test_restart_wait_is_bounded_when_environment_never_readable(tmp_path):
    # MainPID は新しい値だが /proc/<pid>/environ が無い(起動直後で読めない状態が続く)
    proc = _run_script_with_systemctl(
        tmp_path, '#!/bin/bash\ncase "$*" in *MainPID*) echo 300;; esac\nexit 0\n'
    )
    assert "PROC_ENV=unreadable" in proc.stdout
    res = align_send_target("10.42.1.50", "10.42.1.1", runner=_runner_returning(proc.stdout))
    assert not res.ok


def test_align_send_target_works_with_runner_that_takes_only_cmd():
    """provision の runner は cmd だけを受け取る。"""
    seen = []

    def run(cmd):
        seen.append(cmd)
        return _readback()

    assert align_send_target("10.42.1.50", "10.42.1.1", runner=run).ok
    assert len(seen) == 1


# --- B2-T7 IPv4 以外は SSH を呼ばない -------------------------------------------


@pytest.mark.parametrize("bad", ["10.42.1.1; rm -rf /", "10.42.1", "$(id)", "", "10.42.1.1\n"])
def test_align_rejects_non_ipv4_gw_without_running_ssh(bad):
    run = _runner_returning(_readback())
    with pytest.raises(ValueError, match="IPv4"):
        align_send_target("10.42.1.50", bad, runner=run)
    assert run.calls == []


def test_align_remote_script_rejects_non_ipv4_gw():
    with pytest.raises(ValueError, match="IPv4"):
        align_remote_script("10.42.1.1; rm")


@pytest.mark.parametrize("bad", ["-oProxyCommand=x", "a b", "x;y", "", "$(id)"])
def test_align_rejects_unsafe_host_without_running_ssh(bad):
    run = _runner_returning(_readback())
    res = align_send_target(bad, "10.42.1.1", runner=run)
    assert not res.ok
    assert run.calls == []


def test_align_remote_command_is_a_single_quoted_argument():
    run = _runner_returning(_readback())
    align_send_target("zero2.local", "10.42.1.1", runner=run)
    remote = run.calls[0][-1]
    assert shlex.split(remote)[0] == "bash"


# --- B2-T17 契約の記録: destination が変わっても event_id は同じ ------------------


def _load_child_ack_delivery():
    path = REPO_ROOT / "child" / "ack_delivery.py"
    spec = importlib.util.spec_from_file_location("child_ack_delivery_for_b2", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_changing_destination_resends_same_event_id_and_never_reuses_ack(tmp_path):
    """exactly-once の記録(child/ は変更しない)。

    MQTT_HOST を変えると台帳は別 destination になり、旧 destination の ACK は見えない。
    同じ CSV は同じ event_id で新 destination に再送される。Oracle の MERGE が冪等なので
    重複しない。この前提が崩れたら、送り先の付け替えは黙って二重記録を生む。
    """
    ack = _load_child_ack_delivery()
    db = tmp_path / "ack.db"
    rec = {"event_id": "EV-1", "mk_date": "202609291200"}
    old = ack.DeliveryStore(db, "10.42.0.1:1883/presence/record")
    old.save_pending(rec, 1.0)
    old.mark_acked("EV-1", committed_at="202609291201")
    new = ack.DeliveryStore(db, "10.42.1.1:1883/presence/record")
    assert old.destination != new.destination
    assert new.get("EV-1") is None  # 旧 destination の ACK は流用されない
    assert new.status(rec) is None  # 未送信として扱われ、同じ event_id で再送される
    new.save_pending(rec, 2.0)
    assert new.status(rec) == "pending"
    assert new.get("EV-1").payload["event_id"] == rec["event_id"]
    assert old.status(rec) == "acked"  # 旧 destination の記録は残る(二重に ACK 済み扱いにしない)
