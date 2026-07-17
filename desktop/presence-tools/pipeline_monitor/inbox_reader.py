"""bridge の record_inbox(SQLite) を読み取り専用で覗く。書き込みは絶対にしない。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from pipeline_monitor.model import InboxRow, InboxView


class RecordInboxReader:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def read(self, limit: int = 30) -> InboxView:
        if not Path(self.db_path).exists():
            raise FileNotFoundError(self.db_path)
        # WAL 中でも安全に読める read-only オープン。
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            counts = conn.execute(
                "SELECT status, COUNT(*) AS n FROM record_inbox GROUP BY status"
            ).fetchall()
            received = sent = 0
            for r in counts:
                if r["status"] == "received":
                    received = r["n"]
                elif r["status"] == "sent":
                    sent = r["n"]
            cur = conn.execute(
                "SELECT event_id, device_id, mk_date, status, retry_count, "
                "last_error, received_at_iso, sent_at_iso "
                "FROM record_inbox ORDER BY received_at_iso DESC LIMIT ?",
                (limit,),
            )
            rows = [
                InboxRow(
                    event_id=r["event_id"], device_id=r["device_id"],
                    mk_date=r["mk_date"], status=r["status"],
                    retry_count=r["retry_count"], last_error=r["last_error"],
                    received_at_iso=r["received_at_iso"], sent_at_iso=r["sent_at_iso"],
                )
                for r in cur.fetchall()
            ]
            return InboxView(rows=rows, received=received, sent=sent, total=received + sent)
        finally:
            conn.close()
