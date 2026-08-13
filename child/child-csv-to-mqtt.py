#!/usr/bin/env python3
# ruff: noqa: T201  これは操作用CLIなので print 出力は意図的
"""child-csv-to-mqtt.py — 子ラズパイで実行。CSVが来たら1行ずつMQTTへ発行する。

各CSV行:  YYYYMMDDhhmmss,STA_NO1,STA_NO2,STA_NO3,T1_STATUS
   例:    20260610173000,100,200,300,3
を JSON にして、ハブの mosquitto(既定 10.42.0.1:1883) のトピック presence/record へ
QoS2 で発行する。ハブの bridge が Oracle(HHC001) へ書き込む。

冪等性: event_id を「device_id + 行内容」のSHA1から決定的に生成するので、同じ行を
再送しても二重記録されない(bridgeのinbox PK + Oracle MERGEキーで吸収)。

WATCH_DIR の *.csv を処理したら ARCHIVE_DIR へ移動する。常駐(ポーリング)。

環境変数(既定):
  MQTT_HOST=10.42.0.1  MQTT_PORT=1883  TOPIC=presence/record
  DEVICE_ID=$(hostname)  WATCH_DIR=./outbox  ARCHIVE_DIR=./sent
  POLL_SECONDS=5
"""
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("paho-mqtt が必要です:  pip install paho-mqtt")

MQTT_HOST = os.environ.get("MQTT_HOST", "10.42.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
TOPIC = os.environ.get("TOPIC", "presence/record")
DEVICE_ID = os.environ.get("DEVICE_ID") or os.uname().nodename
WATCH_DIR = Path(os.environ.get("WATCH_DIR", "./outbox"))
ARCHIVE_DIR = Path(os.environ.get("ARCHIVE_DIR", "./sent"))
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "5"))

_MK_DATE_RE = re.compile(r"^\d{14}$")
SCHEMA_VERSION = 1


def parse_line(line: str) -> dict | None:
    """CSV1行 -> record dict。不正な行は None(スキップ)。"""
    parts = [p.strip() for p in line.strip().split(",")]
    if len(parts) != 5 or not parts[0]:
        return None
    mk_date, sta1, sta2, sta3, t1 = parts
    if not _MK_DATE_RE.match(mk_date):
        return None
    try:
        t1_status = int(t1)
    except ValueError:
        return None
    event_id = hashlib.sha1(  # noqa: S324  非暗号用途(冪等キー)
        f"{DEVICE_ID}|{mk_date},{sta1},{sta2},{sta3},{t1_status}".encode()
    ).hexdigest()
    return {
        "event_id": event_id,
        "mk_date": mk_date,
        "sta_no1": sta1,
        "sta_no2": sta2,
        "sta_no3": sta3,
        "t1_status": t1_status,
        "device_id": DEVICE_ID,
        "schema_version": SCHEMA_VERSION,
    }


def publish_file(client: "mqtt.Client", path: Path) -> bool:
    """1ファイルの全行を発行。全行発行できたら True。"""
    ok = True
    sent = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        rec = parse_line(raw)
        if rec is None:
            print(f"  skip(不正行): {raw!r}", flush=True)
            continue
        info = client.publish(TOPIC, json.dumps(rec), qos=2)
        info.wait_for_publish(timeout=10)
        if not info.is_published():
            ok = False
            break
        sent += 1
    print(f"  {path.name}: {sent}行 発行 (ok={ok})", flush=True)
    return ok


def main() -> int:
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    client = mqtt.Client(client_id=f"child-csv-{DEVICE_ID}", protocol=mqtt.MQTTv311)
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()
    print(f"child-csv-to-mqtt: {MQTT_HOST}:{MQTT_PORT} {TOPIC} device={DEVICE_ID}", flush=True)
    try:
        while True:
            for path in sorted(WATCH_DIR.glob("*.csv")):
                print(f"処理: {path.name}", flush=True)
                if publish_file(client, path):
                    shutil.move(str(path), str(ARCHIVE_DIR / path.name))
                else:
                    print(f"  発行未完→次回再試行: {path.name}", flush=True)
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
