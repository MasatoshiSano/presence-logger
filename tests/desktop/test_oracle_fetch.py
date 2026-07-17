from pipeline_monitor.oracle_reader import (
    OracleQuery,
    OracleRecentReader,
    build_post_body,
)

Q = OracleQuery(host="10.166.5.93", port="1521", service="HHC001", user="u",
                table="HF1RCM01", sta_no1="100", sta_no2="200", sta_no3="300",
                limit=30)


def test_build_post_body_is_urlencoded_and_has_jdbc_url():
    body = build_post_body(Q, "secret&pw")
    assert "url=jdbc%3Aoracle%3Athin%3A%40" in body
    assert "table_name=HF1RCM01" in body
    assert "sta_no1=100" in body
    assert "limit=30" in body
    assert "secret%26pw" in body          # password url-encoded


def test_fetch_parses_runner_output():
    captured = {}

    def fake_runner(cmd, stdin):
        captured["cmd"] = cmd
        captured["stdin"] = stdin
        return "count=1\nora_code=\nerror_message=\nrow=20260717090000,100,200,300,1,0\n"

    reader = OracleRecentReader("presence-oracle-jdbc", "http://127.0.0.1:8086")
    res = reader.fetch(Q, "pw", runner=fake_runner)
    assert res.ok
    assert res.rows[0].mk_date == "20260717090000"
    assert "presence-oracle-jdbc" in captured["cmd"]
    assert "/select_recent" in " ".join(captured["cmd"])
