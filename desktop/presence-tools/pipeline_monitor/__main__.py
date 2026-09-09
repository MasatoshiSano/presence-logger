"""python -m pipeline_monitor で起動する配線層。"""
from __future__ import annotations

import curses
import os
import subprocess  # noqa: S404

from pipeline_monitor.config import load_oracle_query
from pipeline_monitor.dashboard import Deps, run
from pipeline_monitor.inbox_reader import RecordInboxReader
from pipeline_monitor.mqtt_tail import MqttTail
from pipeline_monitor.oracle_reader import OracleRecentReader

MQTT_HOST = os.environ.get("MQTT_HOST", "10.42.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
INBOX_DB = os.environ.get(
    "RECORD_INBOX_DB", "/var/lib/presence-logger/bridge_record_buf.db")
PROFILES_YAML = os.environ.get("PROFILES_YAML", "/etc/presence-logger/profiles.yaml")
PROFILE_NAME = os.environ.get("PROFILE_NAME", "HIME-H-REAP")
BRIDGE_CONTAINER = os.environ.get("BRIDGE_CONTAINER", "presence-bridge")
JDBC_CONTAINER = os.environ.get("JDBC_CONTAINER", "presence-oracle-jdbc")
SIDECAR_URL = os.environ.get("SIDECAR_IN", "http://127.0.0.1:8086")
PW_ENV = os.environ.get("PW_ENV", "ORACLE_PASSWORD_HHC")
LIMIT = int(os.environ.get("ORACLE_LIMIT", "30"))


def _get_password() -> str:
    out = subprocess.run(  # noqa: S603
        ["docker", "exec", BRIDGE_CONTAINER, "printenv", PW_ENV],  # noqa: S607
        capture_output=True, text=True, check=False,
    )
    return out.stdout.strip()


WAN_IFACE = os.environ.get("WAN_IFACE", "wlan0")


def _get_ssid() -> str:
    # dual-WiFi構成ではwlan1が常時presence-hub AP(子Pi向け)として「active」に
    # 見えるため、`nmcli dev wifi`の先頭yes行を拾うと誤検出する。工場網/自宅網は
    # wlan0固定なので iwgetid でwlan0のESSIDだけを見る。
    out = subprocess.run(  # noqa: S603
        ["iwgetid", "-r", WAN_IFACE],  # noqa: S607
        capture_output=True, text=True, check=False,
    )
    ssid = out.stdout.strip()
    return ssid or "(不明)"


def main() -> int:
    deps = Deps(
        tail=MqttTail(MQTT_HOST, MQTT_PORT),
        inbox=RecordInboxReader(INBOX_DB, bridge_container=BRIDGE_CONTAINER),
        oracle=OracleRecentReader(JDBC_CONTAINER, SIDECAR_URL),
        oracle_query_loader=lambda: load_oracle_query(PROFILES_YAML, PROFILE_NAME, LIMIT),
        password_getter=_get_password,
        ssid_getter=_get_ssid,
        expected_ssid=PROFILE_NAME,
    )
    curses.wrapper(run, deps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
