"""AP インターフェース名が環境変数で差し替えられることを検証する。

新しいハブでドングルが wlan0 として現れると、wlan1 決め打ちのままでは
子が1台も表示されず、wait_for_return も永久に成功しない。
discovery と provision に同じ文字列が二重にあるのも是正する。
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

from fleet_ui import provision


@pytest.fixture(autouse=True)
def restore_ap_dev_after_each_test():
    """reload はモジュールを汚染する。後続スイートが wlan0 のまま残らないように戻す。"""
    yield
    os.environ.pop("AP_DEV", None)
    from fleet_ui import discovery

    importlib.reload(discovery)
    importlib.reload(provision)


def test_ap_dev_defaults_to_wlan1(monkeypatch):
    monkeypatch.delenv("AP_DEV", raising=False)
    from fleet_ui import discovery
    importlib.reload(discovery)
    assert discovery.AP_DEV == "wlan1"


def test_ap_dev_is_overridable(monkeypatch):
    monkeypatch.setenv("AP_DEV", "wlan0")
    from fleet_ui import discovery
    importlib.reload(discovery)
    assert discovery.AP_DEV == "wlan0"


def test_wait_for_return_uses_the_configured_device(monkeypatch):
    monkeypatch.setenv("AP_DEV", "wlan0")
    from fleet_ui import discovery
    importlib.reload(discovery)
    importlib.reload(provision)
    seen = []

    def runner(cmd):
        seen.append(cmd)
        return "10.42.0.9 lladdr aa:bb:cc:dd:ee:ff REACHABLE\n"

    provision.wait_for_return("aa:bb:cc:dd:ee:ff", runner=runner, sleeper=lambda _: None)
    assert seen[0] == ["ip", "-4", "neigh", "show", "dev", "wlan0"]


def test_fleet_ui_service_sets_ap_dev():
    body = Path("fleet_ui/systemd/fleet-ui.service").read_text(encoding="utf-8")
    assert "Environment=AP_DEV=wlan1" in body
