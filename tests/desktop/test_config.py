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
"""


def test_load_oracle_query(tmp_path):
    p = tmp_path / "profiles.yaml"
    p.write_text(_YAML)
    q = load_oracle_query(str(p), "HIME-H-REAP", limit=30)
    assert q.host == "10.166.5.93"
    assert q.service == "HHC001"
    assert q.table == "HF1RCM01"
    assert q.limit == 30
    # 局番はここでは読まない: 子PiのSTA_NOは id_names_config.json 次第で
    # profiles.yaml と一致する保証が無いため(検証時に行自身の値を使う)。
    assert q.sta_no1 == ""
