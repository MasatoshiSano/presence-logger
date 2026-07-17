"""4ペイン curses ダッシュボード。描画とキー処理のみ。ロジックは各 reader が持つ。

ペイン配置:
   ┌ ①子Pi別受信 ─────┬ ②MQTT生ログ ─┐
   ├─────────────────┼─────────────┤
   └ ③record_inbox ──┴ ④Oracle ────┘
"""
from __future__ import annotations

import curses
import time
from collections.abc import Callable
from dataclasses import dataclass

from pipeline_monitor.inbox_reader import RecordInboxReader
from pipeline_monitor.linker import annotate_stages
from pipeline_monitor.mqtt_tail import MqttTail
from pipeline_monitor.oracle_reader import OracleRecentReader, OracleResult
from pipeline_monitor.rollup import child_rollup

ORACLE_REFRESH_S = 15.0
HEARTBEAT_TIMEOUT_S = 60.0


@dataclass
class Deps:
    tail: MqttTail
    inbox: RecordInboxReader
    oracle: OracleRecentReader
    oracle_query_loader: Callable[[], object]     # -> OracleQuery
    password_getter: Callable[[], str]
    ssid_getter: Callable[[], str]


def _safe(fn, default):
    try:
        return fn(), None
    except Exception as e:  # noqa: BLE001  ペインは落とさない
        return default, f"{type(e).__name__}: {e}"


def _addstr(win, y, x, text, attr=0):
    h, w = win.getmaxyx()
    if 0 <= y < h and 0 <= x < w:
        win.addnstr(y, x, text, max(0, w - x - 1), attr)


def _draw_children(win, msgs, now):
    win.erase()
    win.box()
    _addstr(win, 0, 2, " ①子Pi別 受信 ", curses.A_BOLD)
    stats = child_rollup(msgs, now=now, heartbeat_timeout=HEARTBEAT_TIMEOUT_S)
    _addstr(win, 1, 2, f"{'device':<12}{'rec':>4} {'状態':<8}{'hb':>6} 最新mk")
    for i, s in enumerate(stats, start=2):
        badge = {"online": "🟢", "offline": "🔴", "stale": "🟡"}.get(s.state, "⚪")
        hb = f"{int(s.last_heartbeat_age)}s" if s.last_heartbeat_age is not None else "-"
        mk = s.last_record_mk_date or "-"
        _addstr(win, i, 2, f"{s.device_id:<12}{s.record_count:>4} {badge}{s.state:<7}{hb:>6} {mk}")
    win.noutrefresh()


def _draw_mqtt(win, msgs):
    win.erase()
    win.box()
    _addstr(win, 0, 2, " ②MQTT生ログ (presence/#) ", curses.A_BOLD)
    h, _ = win.getmaxyx()
    visible = msgs[-(h - 2):] if len(msgs) > h - 2 else msgs
    for i, m in enumerate(visible, start=1):
        ts = time.strftime("%H:%M:%S", time.localtime(m.ts))
        _addstr(win, i, 2, f"{ts} {m.summary}")
    win.noutrefresh()


def _draw_inbox(win, msgs, inbox_reader):
    win.erase()
    win.box()
    _addstr(win, 0, 2, " ③MQTT→Oracle (record_inbox) ", curses.A_BOLD)
    view, err = _safe(inbox_reader.read, None)
    if err or view is None:
        _addstr(win, 1, 2, f"⚠ 取得失敗: {err}")
        win.noutrefresh()
        return
    linked = annotate_stages(view.rows, {m.event_id for m in msgs if m.event_id})
    _addstr(win, 1, 2, f"received(滞留)={view.received}  sent={view.sent}  合計={view.total}",
            curses.A_BOLD)
    h, _ = win.getmaxyx()
    for i, lk in enumerate(linked[: h - 3], start=2):
        r = lk.inbox
        err_s = f" !{r.last_error[:18]}" if r.last_error else ""
        _addstr(win, i, 2,
                f"{(r.device_id or '?'):<8} {r.event_id[:10]:<10} {lk.stage:<18} "
                f"r{r.retry_count}{err_s}")
    win.noutrefresh()


def _draw_oracle(win, result: OracleResult | None, err, ssid, last_at):
    win.erase()
    win.box()
    _addstr(win, 0, 2, " ④Oracle テーブル ([r]更新) ", curses.A_BOLD)
    if err or result is None:
        _addstr(win, 1, 2, f"⚠ 取得失敗: {err}")
        _addstr(win, 2, 2, f"SSID={ssid}（工場網でないと届きません）")
        win.noutrefresh()
        return
    if not result.ok:
        _addstr(win, 1, 2, f"⚠ ORA-{result.ora_code} {result.error_message[:30]}")
        win.noutrefresh()
        return
    updated = time.strftime("%H:%M:%S", time.localtime(last_at))
    _addstr(win, 1, 2, f"{'日時':<19} {'T1':<5} STA(1/2/3)   最終更新={updated}")
    h, _ = win.getmaxyx()
    for i, r in enumerate(result.rows[: h - 3], start=2):
        mk = r.mk_date
        disp = (f"{mk[0:4]}-{mk[4:6]}-{mk[6:8]} {mk[8:10]}:{mk[10:12]}:{mk[12:14]}"
                if len(mk) == 14 and mk.isdigit() else mk)
        # T1_STATUS は入退室に限らないため生の数値を表示する。
        _addstr(win, i, 2, f"{disp:<19} {r.t1_status:<5} {r.sta_no1}/{r.sta_no2}/{r.sta_no3}")
    win.noutrefresh()


def run(stdscr, deps: Deps) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    deps.tail.start()
    oracle_result: OracleResult | None = None
    oracle_err: str | None = None
    oracle_last = 0.0

    def refresh_oracle():
        nonlocal oracle_result, oracle_err, oracle_last
        def _do():
            q = deps.oracle_query_loader()
            return deps.oracle.fetch(q, deps.password_getter())
        oracle_result, oracle_err = _safe(_do, None)
        oracle_last = time.time()

    try:
        while True:
            now = time.time()
            if now - oracle_last >= ORACLE_REFRESH_S:
                refresh_oracle()

            msgs = deps.tail.messages()
            h, w = stdscr.getmaxyx()
            mid_y, mid_x = h // 2, w // 2
            stdscr.erase()
            _addstr(stdscr, 0, 0,
                    " presence パイプライン監視  [r]Oracle即時 [q]終了 ", curses.A_REVERSE)
            tl = stdscr.derwin(mid_y - 1, mid_x, 1, 0)
            tr = stdscr.derwin(mid_y - 1, w - mid_x, 1, mid_x)
            bl = stdscr.derwin(h - mid_y - 1, mid_x, mid_y, 0)
            br = stdscr.derwin(h - mid_y - 1, w - mid_x, mid_y, mid_x)
            _draw_children(tl, msgs, now)
            _draw_mqtt(tr, msgs)
            _draw_inbox(bl, msgs, deps.inbox)
            _draw_oracle(br, oracle_result, oracle_err, deps.ssid_getter(), oracle_last)
            stdscr.noutrefresh()
            curses.doupdate()

            ch = stdscr.getch()
            if ch in (ord("q"), ord("Q")):
                break
            if ch in (ord("r"), ord("R")):
                refresh_oracle()
            time.sleep(1.0)
    finally:
        deps.tail.stop()
