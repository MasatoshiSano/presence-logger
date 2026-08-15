"""新しい子のホスト名の検証と既定値生成。

ホスト名は device_id と MQTT の client_id の両方を決める
(`child-csv-to-mqtt.py` の `client_id=f"child-csv-{DEVICE_ID}"`)。
既存の子と重複させると、MQTTは同一client_idの新規接続時に既存接続を切断するため、
2台が互いを蹴り合う無限ループになり、元から動いていた方まで巻き込まれる。
実際に2台目投入時に起きた事故なので、保存前に必ず弾く。
"""
from __future__ import annotations

import re

_VALID_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_SEQ_RE = re.compile(r"^(?P<base>.+?)-(?P<n>\d+)$")
_FALLBACK = "pizero2w-2"


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


def suggest_hostname(existing: list[str]) -> str:
    """既存の連番の続きを提案する。例: pizero2w, pizero2w-2 → pizero2w-3"""
    names = [_short(e) for e in existing if e]
    if not names:
        return _FALLBACK

    # 最も多く使われている基底名を採る(同数なら短い方)。
    counts: dict[str, int] = {}
    for n in names:
        m = _SEQ_RE.match(n)
        base = m.group("base") if m else n
        counts[base] = counts.get(base, 0) + 1
    base = max(counts, key=lambda b: (counts[b], -len(b)))

    used: set[int] = set()
    for n in names:
        if n == base:
            used.add(1)
            continue
        m = _SEQ_RE.match(n)
        if m and m.group("base") == base:
            used.add(int(m.group("n")))

    nxt = 2
    while nxt in used:
        nxt += 1
    return f"{base}-{nxt}"
