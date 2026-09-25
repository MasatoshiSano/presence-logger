#!/usr/bin/env python3
# ruff: noqa: T201  これは操作用CLIなので print 出力は意図的
"""child-csv-to-mqtt.py — 子ラズパイで実行。CSVが来たら1行ずつMQTTへ発行する。

各CSV行:  YYYYMMDDhhmmss,STA_NO1,STA_NO2,STA_NO3,T1_STATUS
   例:    20260610173000,100,200,300,3
を JSON にして、ハブの mosquitto(既定 10.42.0.1:1883) のトピック presence/record へ
QoS2 で発行する。ハブの bridge が Oracle(HHC001) へ書き込む。

冪等性: event_id を「device_id + 行内容」のSHA1から決定的に生成するので、同じ行を
再送しても二重記録されない(bridgeのinbox PK + Oracle MERGEキーで吸収)。

送信完了は Oracle commit後のACK(presence/record/ack)を受け取って初めて確定する
(MQTT publish成功だけでは確定しない)。ACK待ちの状態は ACK_DB の台帳で永続化し、
未ACKは resend_after 秒後に同一event_idで再送する。詳細は
docs/2026-09-23-child-oracle-ack-design.md。

WATCH_DIR の *.csv を、全行ACK済みかつ読み取り前後でファイルが変化していない
場合だけ ARCHIVE_DIR へ移動する(archiveは既存ファイルを上書きしない)。常駐(ポーリング)。

環境変数(既定):
  MQTT_HOST=10.42.0.1  MQTT_PORT=1883  TOPIC=presence/record
  DEVICE_ID=$(hostname)  WATCH_DIR=./outbox  ARCHIVE_DIR=./sent
  ACK_DB=./ack.db  POLL_SECONDS=5
"""
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from ack_delivery import AckSession, DeliveryStore, deliver_file

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
NACKED_DIR = Path(os.environ.get("NACKED_DIR", "./nacked"))
ACK_DB = Path(os.environ.get("ACK_DB", "./ack.db"))
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "5"))
# 1巡(WATCH_DIR全体の処理)がACK待ちで無限に伸びないための上限。design参照。
CYCLE_SECONDS = 30.0

_MK_DATE_RE = re.compile(r"^\d{14}$")
SCHEMA_VERSION = 1

# 生存(liveness)トピック。ハブの bridge(liveness.py) と監視TUI(①) が読む。
#   presence/status/<id>   : retained。接続時 online / 切断時 offline
#   presence/heartbeat/<id>: 定期。アプリが生きて heartbeat している証拠
STATUS_TOPIC = f"presence/status/{DEVICE_ID}"
HEARTBEAT_TOPIC = f"presence/heartbeat/{DEVICE_ID}"
HEARTBEAT_SECONDS = float(os.environ.get("HEARTBEAT_SECONDS", "15"))


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


def _choose_target(source: Path, archive_dir: Path) -> Path:
    """archive_dir内でsourceと衝突しない移動先を選ぶ(実際には移動しない)。"""
    target = archive_dir / source.name
    if target.exists():
        n = 1
        while (candidate := archive_dir / f"{source.stem}.{n}{source.suffix}").exists():
            n += 1
        target = candidate
    return target


def archive_file(source: Path, archive_dir: Path) -> Path:
    """sourceをarchive_dirへ移す。同名ファイルが既にあれば上書きせず別名にする。"""
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = _choose_target(source, archive_dir)
    shutil.move(str(source), str(target))
    return target


def _write_nack_sidecar(nacked_dir: Path, target_name: str, rows: list[dict]) -> None:
    """nacked行(line_no・event_id・mk_date・reason・failed_at_iso)を1行1件で書く。
    tmp+renameで、途中で落ちても次回の巡回で同じ名前に上書きされるようにする。
    """
    sidecar = nacked_dir / f"{target_name}.nack.jsonl"
    tmp = sidecar.with_name(sidecar.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row) + "\n")
    tmp.replace(sidecar)


def archive_to_destination(
    path: Path, result, store, archive_dir: Path, nacked_dir: Path
) -> Path | None:
    """resultに応じてCSVの移動先を決めて移す。pendingが残るなら移動せずNone。

    全行acked -> archive_dir(sent)。nackedが1件でもあれば nacked_dir へ移し、
    移動先の名前を決めてから付帯ファイルを書き、最後にCSVを移動する
    (途中で落ちてもCSVはoutboxに残り、次回同じ名前で上書きされる)。
    """
    if not result.complete:
        return None
    if result.nacked == 0:
        return archive_file(path, archive_dir)
    nacked_dir.mkdir(parents=True, exist_ok=True)
    target = _choose_target(path, nacked_dir)
    rows = []
    for line_no, event_id in result.nacked_rows:
        row = store.get(event_id)
        rows.append({
            "line_no": line_no,
            "event_id": event_id,
            "mk_date": row.mk_date if row else None,
            "reason": row.nack_reason if row else None,
            "failed_at_iso": row.nacked_at if row else None,
        })
    _write_nack_sidecar(nacked_dir, target.name, rows)
    shutil.move(str(path), str(target))
    return target


def format_summary(name: str, result) -> str:
    return (
        f"  {name}: ack={result.acked} pending={result.pending} "
        f"invalid={result.invalid} nacked={result.nacked} complete={result.complete}"
    )


def main() -> int:
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    client = mqtt.Client(client_id=f"child-csv-{DEVICE_ID}", protocol=mqtt.MQTTv311)
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    # 異常切断(電源断・NW断)時にブローカーが offline を出す遺言。connect前に設定。
    client.will_set(STATUS_TOPIC, payload="offline", qos=1, retain=True)
    # AckSessionがon_connect/on_subscribe/on_disconnect/on_messageを握るので、
    # connect前に組み立てる(SUBACK受信までtransport.send()はpublishしない)。
    store = DeliveryStore(ACK_DB, f"{MQTT_HOST}:{MQTT_PORT}/{TOPIC}")
    transport = AckSession(client, TOPIC, store)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()
    # 接続直後に online を retained で通知（監視TUIが後から購読しても分かる）。
    client.publish(STATUS_TOPIC, "online", qos=1, retain=True)
    print(f"child-csv-to-mqtt: {MQTT_HOST}:{MQTT_PORT} {TOPIC} device={DEVICE_ID}", flush=True)
    start = time.monotonic()
    last_hb = 0.0
    try:
        while True:
            for path in sorted(WATCH_DIR.glob("*.csv")):
                print(f"処理: {path.name}", flush=True)
                result = deliver_file(path, transport, parse_line, max_seconds=CYCLE_SECONDS)
                print(format_summary(path.name, result), flush=True)
                target = archive_to_destination(path, result, store, ARCHIVE_DIR, NACKED_DIR)
                if target is None:
                    print(f"  未完了→次回再試行: {path.name}", flush=True)
                elif result.nacked:
                    print(
                        f"  {path.name}: nacked/ に移動（恒久失敗 {result.nacked} 行）",
                        flush=True,
                    )
            now = time.monotonic()
            if now - last_hb >= HEARTBEAT_SECONDS:
                client.publish(
                    HEARTBEAT_TOPIC,
                    json.dumps({"uptime_s": int(now - start)}),
                    qos=0,
                )
                last_hb = now
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        # 正常終了は遺言が発火しないので、明示的に offline を出してから切断する。
        info = client.publish(STATUS_TOPIC, "offline", qos=1, retain=True)
        try:
            info.wait_for_publish(timeout=5)
        except (ValueError, RuntimeError):
            pass
        client.loop_stop()
        client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
