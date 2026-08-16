"""子1台の稼働状況を集める。

model_type と status はどちらも /model_status に入っている。
/current_model は機体によっては network/labels しか返さず model_type を持たないため
見てはいけない(実機 zero2 で確認済み。見ると常に不明表示になる)。
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from fleet_ui.discovery import run_cmd

WEB_PORT = 8080


@dataclass(frozen=True)
class ChildStatus:
    ip: str
    hostname: str | None = None
    picamera: str | None = None
    web_server: str | None = None
    model: str | None = None
    ready: str | None = None
    id_names: dict[str, list[str]] = field(default_factory=dict)
    error: str | None = None


def _ssh(ip: str, remote_cmd: str, runner: Callable[[list[str]], str]) -> str:
    return runner([
        "ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", f"pi@{ip}", remote_cmd
    ]).strip()


def collect_status(ip: str, *, runner: Callable[[list[str]], str] = run_cmd) -> ChildStatus:
    """1台分の状況を集める。到達できなければ error に理由を入れて返す(例外にしない)。"""
    hostname = _ssh(ip, "hostname", runner)
    if not hostname:
        return ChildStatus(ip=ip, error="SSHで到達できません")

    picamera = _ssh(ip, "systemctl is-active picamera.service", runner) or "unknown"
    web_server = _ssh(ip, "systemctl is-active web_server.service", runner) or "unknown"

    status_raw = runner([
        "curl", "-sf", "-m", "5", f"http://{ip}:{WEB_PORT}/model_status"
    ])
    model = ready = None
    try:
        d = json.loads(status_raw)
        if isinstance(d, dict):
            model = d.get("model_type") or None
            ready = d.get("status") or None
    except (ValueError, TypeError):
        pass

    id_names: dict[str, list[str]] = {}
    try:
        d = json.loads(_ssh(ip, "cat ~/id_names_config.json", runner))
        got = d.get("id_names") if isinstance(d, dict) else None
        if isinstance(got, dict):
            id_names = got
    except (ValueError, TypeError):
        pass

    return ChildStatus(
        ip=ip, hostname=hostname, picamera=picamera, web_server=web_server,
        model=model, ready=ready, id_names=id_names,
    )
