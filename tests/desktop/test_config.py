import pytest
from pipeline_monitor.config import load_oracle_query

_YAML = """
profiles:
  HIME-H-REAP:
    oracle:
      host: 10.166.5.93
      port: 1521
      service_name: HHC001
      user: HHCUSER
      table_name: HF1RCM01
    station:
      sta_no1: "100"
      sta_no2: "200"
      sta_no3: "300"
"""

_YAML_NO_STATION = """
profiles:
  HIME-H-REAP:
    oracle: {host: h, service_name: s, user: u, table_name: t}
"""


def test_load_oracle_query(tmp_path):
    p = tmp_path / "profiles.yaml"
    p.write_text(_YAML)
    q = load_oracle_query(str(p), "HIME-H-REAP", limit=30)
    assert q.host == "10.166.5.93"
    assert q.service == "HHC001"
    assert q.table == "HF1RCM01"
    assert q.sta_no1 == "100"
    assert q.limit == 30


def test_missing_station_raises(tmp_path):
    p = tmp_path / "profiles.yaml"
    p.write_text(_YAML_NO_STATION)
    with pytest.raises(ValueError, match="station"):
        load_oracle_query(str(p), "HIME-H-REAP", limit=30)
