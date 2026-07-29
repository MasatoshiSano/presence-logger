"""profiles.yaml から非秘密の Oracle 接続情報を読む（既存ツールと同方式）。

sta_no1-3 はここでは読まない: 子Piが送ってくる局番は id_names_config.json 側で
自由に設定され、profiles.yaml の値と一致する保証が無いため。Oracle照会で局番が
要る場合(verify_exact)は record_inbox に記録済みの、その行自身の値を使う。
"""
from __future__ import annotations

import yaml

from pipeline_monitor.oracle_reader import OracleQuery


def load_oracle_query(profiles_yaml: str, profile_name: str, limit: int) -> OracleQuery:
    with open(profiles_yaml) as f:
        data = yaml.safe_load(f) or {}
    profile = (data.get("profiles") or {}).get(profile_name) or {}
    oracle = profile.get("oracle") or {}
    return OracleQuery(
        host=str(oracle.get("host", "")),
        port=str(oracle.get("port", "1521")),
        service=str(oracle.get("service_name", "")),
        user=str(oracle.get("user", "")),
        table=str(oracle.get("table_name", "HF1RCM01")),
        limit=limit,
    )
