"""bridge の record_inbox(SQLite) を読み取り専用で覗く。書き込みは絶対にしない。

本番の DB は root 所有かつ WAL モードなので、pi ユーザーの ?mode=ro 直読みは
「attempt to write a readonly database」で失敗する（WAL 読取には -shm への
書込権限が要るため）。その場合は bridge コンテナ内（root なので WAL を正しく
読める）へ docker exec でフォールバックする。既存の Oracle ペインと同じ
「所有プロセス経由で覗く」方式（pi は docker グループなので exec 可能）。
"""
from __future__ import annotations

import sqlite3
import subprocess  # noqa: S404
from collections.abc import Callable
from pathlib import Path

from pipeline_monitor.model import DeviceAgg, InboxRow, InboxView

# bridge コンテナ内で実行する読取スクリプト（タブ区切りで stdout に出す）。
# -c で渡すのでコンテナ側 python が \t/\n をタブ・改行として解釈する。
_CONTAINER_QUERY = r'''
import sqlite3, sys
db, limit = sys.argv[1], int(sys.argv[2])
c = sqlite3.connect(db); c.row_factory = sqlite3.Row
for r in c.execute("select status, count(*) n from record_inbox group by status"):
    print("CNT\t%s\t%d" % (r["status"], r["n"]))
for r in c.execute("select device_id, count(*) n, max(mk_date) mk "
                   "from record_inbox group by device_id"):
    print("DEV\t%s\t%d\t%s" % (r["device_id"] or "", r["n"], r["mk"] or ""))
q = ("select event_id, device_id, mk_date, status, retry_count, last_error, "
     "received_at_iso, sent_at_iso from record_inbox order by received_at_iso desc limit ?")
for r in c.execute(q, (limit,)):
    le = (r["last_error"] or "").replace("\t", " ").replace("\n", " ")
    print("ROW\t%s\t%s\t%s\t%s\t%d\t%s\t%s\t%s" % (
        r["event_id"], r["device_id"] or "", r["mk_date"], r["status"],
        r["retry_count"], le, r["received_at_iso"], r["sent_at_iso"] or ""))
'''


def _default_runner(cmd: list[str]) -> str:
    proc = subprocess.run(  # noqa: S603
        cmd, capture_output=True, text=True, timeout=15, check=False,
    )
    if proc.returncode != 0 and not proc.stdout:
        raise RuntimeError(proc.stderr.strip() or "docker exec が失敗しました")
    return proc.stdout


def parse_container_output(text: str) -> InboxView:
    """bridge コンテナ内スクリプトのタブ区切り出力を InboxView に変換する（純関数）。"""
    received = sent = 0
    rows: list[InboxRow] = []
    devices: list[DeviceAgg] = []
    for line in text.splitlines():
        parts = line.split("\t")
        if parts[0] == "CNT" and len(parts) == 3:
            if parts[1] == "received":
                received = int(parts[2])
            elif parts[1] == "sent":
                sent = int(parts[2])
        elif parts[0] == "DEV" and len(parts) == 4:
            devices.append(DeviceAgg(
                device_id=parts[1] or "(不明)",
                count=int(parts[2]),
                last_mk_date=parts[3] or None,
            ))
        elif parts[0] == "ROW" and len(parts) == 9:
            rows.append(InboxRow(
                event_id=parts[1],
                device_id=parts[2] or None,
                mk_date=parts[3],
                status=parts[4],
                retry_count=int(parts[5]),
                last_error=parts[6] or None,
                received_at_iso=parts[7],
                sent_at_iso=parts[8] or None,
            ))
    return InboxView(rows=rows, received=received, sent=sent,
                     total=received + sent, devices=devices)


class RecordInboxReader:
    def __init__(
        self,
        db_path: str,
        *,
        bridge_container: str = "presence-bridge",
        runner: Callable[[list[str]], str] = _default_runner,
    ):
        self.db_path = db_path
        self._container = bridge_container
        self._runner = runner

    def read(self, limit: int = 30) -> InboxView:
        if not Path(self.db_path).exists():
            raise FileNotFoundError(self.db_path)
        try:
            return self._read_direct(limit)
        except sqlite3.OperationalError:
            # root 所有 WAL DB を pi が直読みできないケース → コンテナ経由。
            return self._read_via_container(limit)

    def _read_direct(self, limit: int) -> InboxView:
        # WAL 中でも安全に読める read-only オープン（権限が許す場合）。
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            received = sent = 0
            for r in conn.execute(
                "SELECT status, COUNT(*) AS n FROM record_inbox GROUP BY status"
            ).fetchall():
                if r["status"] == "received":
                    received = r["n"]
                elif r["status"] == "sent":
                    sent = r["n"]
            devices = [
                DeviceAgg(
                    device_id=(r["device_id"] or "(不明)"),
                    count=r["n"], last_mk_date=r["mk"],
                )
                for r in conn.execute(
                    "SELECT device_id, COUNT(*) AS n, MAX(mk_date) AS mk "
                    "FROM record_inbox GROUP BY device_id"
                ).fetchall()
            ]
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
            return InboxView(rows=rows, received=received, sent=sent,
                             total=received + sent, devices=devices)
        finally:
            conn.close()

    def _read_via_container(self, limit: int) -> InboxView:
        cmd = [
            "docker", "exec", self._container, "python3", "-c", _CONTAINER_QUERY,
            self.db_path, str(limit),
        ]
        return parse_container_output(self._runner(cmd))
