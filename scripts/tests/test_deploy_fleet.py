"""フリート直列配布ドライバの検証。

各子への実処理は既存 deploy-child.sh が担うため、ここではループ制御
（順序・fail-fast・--only・サマリ・終了コード）だけを偽スクリプトで検証する。
"""
import os

from scripts.tests.shellhelp import run_bash


def _inventory(tmp_path, hosts: list[str]):
    f = tmp_path / "children.conf"
    f.write_text("".join(f"{h}\n" for h in hosts), encoding="utf-8")
    return f


def _stub(tmp_path, name: str, body: str):
    p = tmp_path / name
    p.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    p.chmod(0o755)
    return p


def _env(tmp_path, inv, child_stub):
    log = tmp_path / "fleet.log"
    log.touch()  # 1台も実行されなかった場合でも read_text() できるようにする
    e = dict(os.environ)
    e["FLEET_INVENTORY"] = str(inv)
    e["FLEET_CHILD_SCRIPT"] = str(child_stub)
    e["FLEET_LOG"] = str(log)
    return e


def test_visits_every_host_in_order(tmp_path):
    inv = _inventory(tmp_path, ["a", "b", "c"])
    stub = _stub(tmp_path, "child.sh", 'echo "$CHILD_SSH" >> "$FLEET_LOG"; exit 0')
    env = _env(tmp_path, inv, stub)
    proc = run_bash("scripts/deploy-fleet.sh app", env=env, check=False)
    assert proc.returncode == 0
    assert (tmp_path / "fleet.log").read_text().split() == ["a", "b", "c"]


def test_stops_after_first_failure_by_default(tmp_path):
    inv = _inventory(tmp_path, ["a", "b", "c"])
    stub = _stub(
        tmp_path, "child.sh",
        'echo "$CHILD_SSH" >> "$FLEET_LOG"; [ "$CHILD_SSH" = b ] && exit 1; exit 0',
    )
    env = _env(tmp_path, inv, stub)
    proc = run_bash("scripts/deploy-fleet.sh app", env=env, check=False)
    assert proc.returncode == 1
    assert (tmp_path / "fleet.log").read_text().split() == ["a", "b"]


def test_keep_going_continues_past_failure(tmp_path):
    inv = _inventory(tmp_path, ["a", "b", "c"])
    stub = _stub(
        tmp_path, "child.sh",
        'echo "$CHILD_SSH" >> "$FLEET_LOG"; [ "$CHILD_SSH" = b ] && exit 1; exit 0',
    )
    env = _env(tmp_path, inv, stub)
    proc = run_bash("scripts/deploy-fleet.sh app --keep-going", env=env, check=False)
    assert proc.returncode == 1
    assert (tmp_path / "fleet.log").read_text().split() == ["a", "b", "c"]


def test_only_limits_targets(tmp_path):
    inv = _inventory(tmp_path, ["a", "b", "c"])
    stub = _stub(tmp_path, "child.sh", 'echo "$CHILD_SSH" >> "$FLEET_LOG"; exit 0')
    env = _env(tmp_path, inv, stub)
    run_bash("scripts/deploy-fleet.sh app --only a,c", env=env, check=False)
    assert (tmp_path / "fleet.log").read_text().split() == ["a", "c"]


def test_only_rejects_unknown_host(tmp_path):
    inv = _inventory(tmp_path, ["a"])
    stub = _stub(tmp_path, "child.sh", "exit 0")
    env = _env(tmp_path, inv, stub)
    proc = run_bash("scripts/deploy-fleet.sh app --only zzz", env=env, check=False)
    assert proc.returncode != 0


def test_passes_through_flags_to_child_script(tmp_path):
    inv = _inventory(tmp_path, ["a"])
    stub = _stub(tmp_path, "child.sh", 'printf "%s\\n" "$@" >> "$FLEET_LOG"; exit 0')
    env = _env(tmp_path, inv, stub)
    run_bash("scripts/deploy-fleet.sh app --dry-run --with-shared-config", env=env, check=False)
    logged = (tmp_path / "fleet.log").read_text().split()
    assert "--dry-run" in logged
    assert "--with-shared-config" in logged


def test_does_not_leak_ip_between_hosts(tmp_path):
    """前の子のIPを次の子に持ち越すと、誤ったホストをヘルスチェックし得る。"""
    inv = _inventory(tmp_path, ["a", "b"])
    stub = _stub(tmp_path, "child.sh", 'echo "[$CHILD_AP_IP]" >> "$FLEET_LOG"; exit 0')
    env = _env(tmp_path, inv, stub)
    env["CHILD_AP_IP"] = "10.42.0.99"
    run_bash("scripts/deploy-fleet.sh app", env=env, check=False)
    assert (tmp_path / "fleet.log").read_text().split() == ["[]", "[]"]


def test_model_mode_forwards_name_and_version(tmp_path):
    inv = _inventory(tmp_path, ["a"])
    stub = _stub(tmp_path, "model.sh", 'printf "%s\\n" "$@" >> "$FLEET_LOG"; exit 0')
    env = _env(tmp_path, inv, _stub(tmp_path, "child.sh", "exit 0"))
    env["FLEET_MODEL_SCRIPT"] = str(stub)
    proc = run_bash("scripts/deploy-fleet.sh model signal_tower 20260422", env=env, check=False)
    assert proc.returncode == 0
    assert (tmp_path / "fleet.log").read_text().split() == ["signal_tower", "20260422"]


def test_model_mode_requires_name_and_version(tmp_path):
    inv = _inventory(tmp_path, ["a"])
    env = _env(tmp_path, inv, _stub(tmp_path, "child.sh", "exit 0"))
    proc = run_bash("scripts/deploy-fleet.sh model signal_tower", env=env, check=False)
    assert proc.returncode != 0


def test_empty_inventory_fails_instead_of_silently_succeeding(tmp_path):
    """0台に配って成功扱いで終わるのが最悪。必ずエラーで止まること。"""
    inv = _inventory(tmp_path, [])
    inv.write_text("# コメントだけ\n\n", encoding="utf-8")
    stub = _stub(tmp_path, "child.sh", 'echo "$CHILD_SSH" >> "$FLEET_LOG"; exit 0')
    env = _env(tmp_path, inv, stub)
    proc = run_bash("scripts/deploy-fleet.sh app", env=env, check=False)
    assert proc.returncode != 0
    assert (tmp_path / "fleet.log").read_text() == ""


def test_missing_inventory_fails(tmp_path):
    stub = _stub(tmp_path, "child.sh", "exit 0")
    env = _env(tmp_path, tmp_path / "nope.conf", stub)
    proc = run_bash("scripts/deploy-fleet.sh app", env=env, check=False)
    assert proc.returncode != 0


def test_only_with_empty_value_is_rejected(tmp_path):
    """--only= が空のとき、フィルタを黙って捨ててフリート全体へ配ってはいけない。"""
    inv = _inventory(tmp_path, ["a", "b"])
    stub = _stub(tmp_path, "child.sh", 'echo "$CHILD_SSH" >> "$FLEET_LOG"; exit 0')
    env = _env(tmp_path, inv, stub)
    proc = run_bash("scripts/deploy-fleet.sh app --only=", env=env, check=False)
    assert proc.returncode != 0
    assert (tmp_path / "fleet.log").read_text() == ""


def test_model_mode_rejects_flag_before_name(tmp_path):
    """フラグを <name> <version> より前に置くと、無視されたうえ名前が化ける。"""
    inv = _inventory(tmp_path, ["a"])
    model_stub = _stub(tmp_path, "model.sh", 'printf "%s|" "$@" >> "$FLEET_LOG"; exit 0')
    env = _env(tmp_path, inv, _stub(tmp_path, "child.sh", "exit 0"))
    env["FLEET_MODEL_SCRIPT"] = str(model_stub)
    proc = run_bash(
        "scripts/deploy-fleet.sh model --keep-going signal_tower 20260422",
        env=env, check=False,
    )
    assert proc.returncode != 0
    assert (tmp_path / "fleet.log").read_text() == ""


def test_only_deduplicates_repeated_hosts(tmp_path):
    """--only は利用者入力なのでパーサの重複除去を通らない。ここでも潰すこと。"""
    inv = _inventory(tmp_path, ["a", "b"])
    stub = _stub(tmp_path, "child.sh", 'echo "$CHILD_SSH" >> "$FLEET_LOG"; exit 0')
    env = _env(tmp_path, inv, stub)
    run_bash("scripts/deploy-fleet.sh app --only a,a", env=env, check=False)
    assert (tmp_path / "fleet.log").read_text().split() == ["a"]
