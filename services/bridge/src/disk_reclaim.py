"""SD がいっぱいになっても MQTT ログは止めない。Oracle 受理済みだけ捨てる。

`status=sent` は MERGE 成功（= Oracle 到達）なのでローカルコピーは不要。
`received`（未送信）と heartbeat/status は残す。空きが減ったら回転済み
`*.log.N` も消す（今のログファイルへの追記は続ける）。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

WARN_FREE_BYTES = 1 * 1024 * 1024 * 1024       # 1 GiB
CRITICAL_FREE_BYTES = 256 * 1024 * 1024        # 256 MiB
KEEP_SENT_NORMAL = 500
KEEP_SENT_WARN = 50
KEEP_SENT_CRITICAL = 0

_ROTATED_LOG = re.compile(r"^(bridge|detector|child-mqtt)\.log\.[1-9]\d*$")


def free_bytes(path: str | Path) -> int:
    """Return free bytes on the filesystem containing `path`. 0 on error."""
    try:
        target = Path(path)
        if not target.exists():
            target = target.parent
        return shutil.disk_usage(target).free
    except OSError:
        return 0


def keep_sent_for_free(free: int) -> int:
    if free >= WARN_FREE_BYTES:
        return KEEP_SENT_NORMAL
    if free >= CRITICAL_FREE_BYTES:
        return KEEP_SENT_WARN
    return KEEP_SENT_CRITICAL


def rotated_log_paths(log_dir: str | Path) -> list[Path]:
    root = Path(log_dir)
    try:
        names = list(root.iterdir())
    except OSError:
        return []
    return sorted(p for p in names if p.is_file() and _ROTATED_LOG.match(p.name))


def unlink_rotated_logs(log_dir: str | Path) -> list[str]:
    removed: list[str] = []
    for path in rotated_log_paths(log_dir):
        try:
            path.unlink()
            removed.append(path.name)
        except OSError:
            continue
    return removed
