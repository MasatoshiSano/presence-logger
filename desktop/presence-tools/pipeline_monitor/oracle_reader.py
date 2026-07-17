"""oracle-jdbc サイドカー /select_recent 応答(key=value テキスト)の解析（純関数）。

応答フォーマット（既存 _render_recent.py と同一）:
    count=N
    ora_code=            (空 or ORA番号)
    error_message=
    row=MK_DATE,STA_NO1,STA_NO2,STA_NO3,T1_STATUS,UPCMPFLG
    ...(最新順 / DESC)
"""
from __future__ import annotations

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
