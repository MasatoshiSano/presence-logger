"""AP インターフェース名が環境変数で差し替えられることを検証する。

新しいハブでドングルが wlan0 として現れると、wlan1 決め打ちのままでは
子が1台も表示されず、wait_for_return も永久に成功しない。
discovery と provision に同じ文字列が二重にあるのも是正する。

値は import 時ではなく呼び出し時に読む(current_ap_dev)。import 時に固めると
reload しない限り環境変数が効かず、テストの都合がそのまま実装の制約になる。
argv へそのまま渡すので、変な値はここで既定へ倒す。
"""
from __future__ import annotations

from pathlib import Path

from fleet_ui import provision
from fleet_ui.discovery import AP_DEV, current_ap_dev


def test_ap_dev_defaults_to_wlan1(monkeypatch):
    monkeypatch.delenv("AP_DEV", raising=False)
    assert current_ap_dev() == "wlan1"
    assert AP_DEV == "wlan1"


def test_ap_dev_is_overridable(monkeypatch):
    monkeypatch.setenv("AP_DEV", "wlan0")
    assert current_ap_dev() == "wlan0"


def test_blank_ap_dev_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("AP_DEV", "   ")
    assert current_ap_dev() == "wlan1"


def test_shell_metacharacters_do_not_reach_argv(monkeypatch):
    # discovery は値を ip(8) の argv に渡す。空白や ; が通ると引数が割れる。
    monkeypatch.setenv("AP_DEV", "wlan0; rm -rf /")
    assert current_ap_dev() == "wlan1"


def test_wait_for_return_uses_the_configured_device(monkeypatch):
    monkeypatch.setenv("AP_DEV", "wlan0")
    seen = []

    def runner(cmd):
        seen.append(cmd)
        return "10.42.0.9 lladdr aa:bb:cc:dd:ee:ff REACHABLE\n"

    provision.wait_for_return("aa:bb:cc:dd:ee:ff", runner=runner, sleeper=lambda _: None)
    assert seen[0] == ["ip", "-4", "neigh", "show", "dev", "wlan0"]


def test_fleet_ui_service_sets_ap_dev():
    body = Path("fleet_ui/systemd/fleet-ui.service").read_text(encoding="utf-8")
    assert "Environment=AP_DEV=wlan1" in body
