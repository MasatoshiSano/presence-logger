from pipeline_monitor.oracle_reader import parse_select_recent


def test_parse_ok_rows_most_recent_first():
    text = (
        "count=2\n"
        "ora_code=\n"
        "error_message=\n"
        "row=20260717092300,100,200,300,1,0\n"
        "row=20260717091000,100,200,300,2,0\n"
    )
    res = parse_select_recent(text)
    assert res.ok
    assert res.count == "2"
    assert len(res.rows) == 2
    assert res.rows[0].mk_date == "20260717092300"
    assert res.rows[0].t1_status == "1"
    assert res.rows[0].upcmpflg == "0"


def test_parse_error_response():
    text = "count=0\nora_code=12514\nerror_message=TNS listener\n"
    res = parse_select_recent(text)
    assert not res.ok
    assert res.ora_code == "12514"
    assert "TNS" in res.error_message


def test_parse_empty_result():
    res = parse_select_recent("count=0\nora_code=\nerror_message=\n")
    assert res.ok
    assert res.rows == []
    assert res.count == "0"


def test_row_with_missing_upcmpflg_does_not_crash():
    res = parse_select_recent("row=20260717092300,100,200,300,1\n")
    assert res.rows[0].upcmpflg == ""
