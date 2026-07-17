"""profiles.yaml から非秘密の Oracle 接続情報を読む（既存ツールと同方式）。"""
from __future__ import annotations

import yaml

from pipeline_monitor.oracle_reader import OracleQuery


def load_oracle_query(profiles_yaml: str, profile_name: str, limit: int) -> OracleQuery:
    with open(profiles_yaml) as f:
        data = yaml.safe_load(f) or {}
    profile = (data.get("profiles") or {}).get(profile_name) or {}
    oracle = profile.get("oracle") or {}
    station = profile.get("station") or {}
    s1, s2, s3 = station.get("sta_no1"), station.get("sta_no2"), station.get("sta_no3")
    if not (s1 and s2 and s3):
        raise ValueError(
            f"{profile_name} の station(sta_no1/2/3) が profiles.yaml にありません"
        )
    return OracleQuery(
        host=str(oracle.get("host", "")),
        port=str(oracle.get("port", "1521")),
        service=str(oracle.get("service_name", "")),
        user=str(oracle.get("user", "")),
        table=str(oracle.get("table_name", "HF1RCM01")),
        sta_no1=str(s1), sta_no2=str(s2), sta_no3=str(s3), limit=limit,
    )
