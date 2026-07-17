"""merged_children: ①子Pi別受信を DB件数を真実にし、MQTT liveness を重ねる。"""
from pipeline_monitor.model import DeviceAgg, MqttMsg
from pipeline_monitor.rollup import merged_children

TIMEOUT = 60.0


def _hb(dev, ts):
    return MqttMsg(ts=ts, topic=f"presence/heartbeat/{dev}", kind="heartbeat",
                   device_id=dev, event_id=None, summary="", raw="")


def test_db_device_appears_even_with_no_live_mqtt():
    # 監視起動後に新規送信ゼロ（msgs空）でも、DBの子は消えない。
    devs = [DeviceAgg("pizero2w", 120, "20260717091000")]
    out = merged_children(devs, [], now=1000.0, heartbeat_timeout=TIMEOUT)
    assert len(out) == 1
    s = out[0]
    assert s.device_id == "pizero2w"
    assert s.record_count == 120                 # DBの総件数
    assert s.last_record_mk_date == "20260717091000"
    assert s.state == "unknown"                  # heartbeat 無し
    assert s.last_heartbeat_age is None


def test_db_count_wins_and_liveness_overlaid_from_mqtt():
    devs = [DeviceAgg("pizero2w", 120, "20260717091000")]
    msgs = [_hb("pizero2w", 980.0)]
    out = merged_children(devs, msgs, now=1000.0, heartbeat_timeout=TIMEOUT)
    s = out[0]
    assert s.record_count == 120                 # DB件数（MQTTの生カウントで上書きしない）
    assert s.state == "online"                   # heartbeat が新しい
    assert s.last_heartbeat_age is not None


def test_mqtt_only_device_not_in_db_is_included():
    # DBにまだ無いが heartbeat だけ来ている子も出す。
    out = merged_children([], [_hb("newpi", 990.0)], now=1000.0, heartbeat_timeout=TIMEOUT)
    assert [s.device_id for s in out] == ["newpi"]
    assert out[0].state == "online"


def test_sorted_by_device_id():
    devs = [DeviceAgg("zero2", 1, None), DeviceAgg("aaa", 2, None)]
    out = merged_children(devs, [], now=1000.0, heartbeat_timeout=TIMEOUT)
    assert [s.device_id for s in out] == ["aaa", "zero2"]
