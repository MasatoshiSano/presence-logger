"""oracle-jdbc サイドカー /select_recent 応答(key=value テキスト)の解析（純関数）。

応答フォーマット（既存 _render_recent.py と同一）:
    count=N
    ora_code=            (空 or ORA番号)
    error_message=
    row=MK_DATE,STA_NO1,STA_NO2,STA_NO3,T1_STATUS,UPCMPFLG
    ...(最新順 / DESC)
"""
from __future__ import annotations

import subprocess  # noqa: S404
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass

from pipeline_monitor.model import OracleResult, OracleRow


def parse_select_recent(text: str) -> OracleResult:
    res = OracleResult()
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if line.startswith("count="):
            res.count = line[len("count="):]
        elif line.startswith("ora_code="):
            res.ora_code = line[len("ora_code="):]
        elif line.startswith("error_message="):
            res.error_message = line[len("error_message="):]
        elif line.startswith("row="):
            parts = line[len("row="):].split(",", 5)
            parts += [""] * (6 - len(parts))   # 欠けた列は空で埋める
            res.rows.append(OracleRow(
                mk_date=parts[0], sta_no1=parts[1], sta_no2=parts[2],
                sta_no3=parts[3], t1_status=parts[4], upcmpflg=parts[5],
            ))
    return res


@dataclass
class OracleQuery:
    host: str
    port: str
    service: str
    user: str
    table: str
    sta_no1: str
    sta_no2: str
    sta_no3: str
    limit: int = 30


def build_post_body(q: OracleQuery, password: str) -> str:
    return urllib.parse.urlencode({
        "url": f"jdbc:oracle:thin:@{q.host}:{q.port}/{q.service}",
        "user": q.user,
        "password": password,
        "table_name": q.table,
        "sta_no1": q.sta_no1,
        "sta_no2": q.sta_no2,
        "sta_no3": q.sta_no3,
        "limit": str(q.limit),
    })


def _default_runner(cmd: list[str], stdin: str) -> str:
    # サイドカーは presence-net 内のみ待受なので docker exec 経由で叩く。
    proc = subprocess.run(  # noqa: S603
        cmd, input=stdin, capture_output=True, text=True, timeout=45, check=False,
    )
    return proc.stdout


class OracleRecentReader:
    def __init__(self, jdbc_container: str, sidecar_url: str):
        self._container = jdbc_container
        self._url = sidecar_url

    def fetch(
        self,
        q: OracleQuery,
        password: str,
        runner: Callable[[list[str], str], str] = _default_runner,
    ) -> OracleResult:
        body = build_post_body(q, password)
        cmd = [
            "docker", "exec", "-i", self._container, "wget", "-q", "--timeout=40",
            "--header=Content-Type: application/x-www-form-urlencoded",
            f"--post-data={body}", "-O", "-", f"{self._url}/select_recent",
        ]
        return parse_select_recent(runner(cmd, ""))
