"""子の稼働状況収集の検証。

model_type は /model_status から読む。/current_model は機体によっては
network/labels しか返さず model_type を持たないため(実機 zero2 で確認)、
そちらを見ると常に不明表示になる。
"""
from fleet_ui.collect import collect_status


def _runner(mapping):
    """cmd の内容に応じて決め打ちの出力を返す偽 runner。"""
    def run(cmd):
        joined = " ".join(cmd)
        for key, val in mapping.items():
            if key in joined:
                return val
        return ""
    return run


def test_collects_services_and_model():
    r = _runner({
        "hostname": "pizero2w\n",
        "is-active picamera": "active\n",
        "is-active web_server": "active\n",
        "model_status": '{"status": "ready", "model_type": "signal_tower"}',
        "id_names_config.json": '{"id_names": {"1": ["HIME", "T120", "004020"]}}',
    })
    s = collect_status("10.42.0.52", runner=r)
    assert s.hostname == "pizero2w"
    assert s.picamera == "active"
    assert s.model == "signal_tower"
    assert s.ready == "ready"
    assert s.id_names == {"1": ["HIME", "T120", "004020"]}
    assert s.error is None


def test_model_comes_from_model_status_not_current_model():
    """/current_model に model_type が無い機体でもモデル名が出ること。"""
    r = _runner({
        "hostname": "pizero2w\n",
        "is-active": "active\n",
        "model_status": '{"status": "ready", "model_type": "signal_tower"}',
        "current_model": '{"network": "/home/pi/signal_tower/network.rpk"}',
        "id_names_config.json": '{"id_names": {}}',
    })
    assert collect_status("10.42.0.52", runner=r).model == "signal_tower"


def test_unreachable_child_reports_error_without_raising():
    s = collect_status("10.42.0.99", runner=lambda cmd: "")
    assert s.error is not None
    assert s.hostname is None


def test_broken_id_names_does_not_crash():
    """子の設定が壊れていても落ちない。id_names は空として扱う。"""
    r = _runner({
        "hostname": "pizero2w\n",
        "is-active": "active\n",
        "model_status": '{"status": "ready"}',
        "id_names_config.json": '{"id_names": "こわれている"}',
    })
    assert collect_status("10.42.0.52", runner=r).id_names == {}
