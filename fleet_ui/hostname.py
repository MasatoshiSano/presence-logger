"""新しい子のホスト名の検証と既定値生成。

ホスト名は device_id と MQTT の client_id の両方を決める
(`child-csv-to-mqtt.py` の `client_id=f"child-csv-{DEVICE_ID}"`)。
既存の子と重複させると、MQTTは同一client_idの新規接続時に既存接続を切断するため、
2台が互いを蹴り合う無限ループになり、元から動いていた方まで巻き込まれる。
実際に2台目投入時に起きた事故なので、保存前に必ず弾く。
"""
from __future__ import annotations

import re
from pathlib import Path

_VALID_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?\Z")
_FALLBACK_HUB = "child"


def _short(name: str) -> str:
    """インベントリのエントリからホスト名部分を取り出す(`.local` を落とす)。"""
    return name[: -len(".local")] if name.endswith(".local") else name


def validate_hostname(name: str, existing: list[str]) -> str | None:
    """問題があれば日本語の理由を返す。問題なければ None。"""
    if not name:
        return "ホスト名を入力してください"
    if len(name) > 63:
        return "ホスト名が長すぎます(63文字まで)"
    if not _VALID_RE.match(name):
        return "英小文字・数字・ハイフンのみが使えます。先頭と末尾はハイフン以外にしてください"
    if name in {_short(e) for e in existing}:
        return (
            f"'{name}' は既に使われています。同名だと MQTT の client_id が衝突し、"
            "2台が互いの接続を切断し合います"
        )
    return None


def read_hub_hostname(path: Path) -> str:
    """site.env の HUB_HOSTNAME。無ければ空。"""
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.split("#", 1)[0].strip()
        if s.startswith("HUB_HOSTNAME="):
            return s.split("=", 1)[1].strip().strip("\"'")
    return ""


def suggest_hostname(existing: list[str], hub: str = "") -> str:
    """このハブの N 台目の子。例: ハブ tpc12345、名簿が1台 → tpc12345-2。

    N はいまの名簿の台数+1。同じ名前が既にあれば一つ進める。
    """
    hub = _short((hub or "").strip().lower())
    if not hub or not _VALID_RE.match(hub):
        hub = _FALLBACK_HUB

    names = {_short(e) for e in existing if e}
    names.add(hub)
    n = len([e for e in existing if e]) + 1
    if n < 1:
        n = 1
    while n <= 9999:
        cand = f"{hub}-{n}"
        if cand not in names and len(cand) <= 63:
            return cand
        n += 1
    return f"{_FALLBACK_HUB}-1"
