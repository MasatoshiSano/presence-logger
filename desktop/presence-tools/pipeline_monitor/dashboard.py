"""4ペイン curses ダッシュボード。描画とキー処理のみ。ロジックは各 reader が持つ。

ペイン配置:
   ┌ ①子Pi別受信 ─────┬ ②MQTT生ログ ─────┐
   ├─────────────────┼─────────────────┤
   └ ③record_inbox ──┴ ④アップロード履歴 ┘

④は record_inbox の status=sent 行(=bridgeがOracle受理と判断した行)を、局番を
先読みせずそのまま表示する。子PiのSTA_NO1-3は id_names_config.json 次第で
何が来るか分からないため、固定フィルタでのOracle SELECTはしない(§関連の議論)。
SSIDが一致しているときだけ、行ごとに自分のSTA_NO/MK_DATEでOracleへの実在を
1行ずつ検証し、✓/⚠を付ける。
"""
from __future__ import annotations

import curses
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from pipeline_monitor.inbox_reader import RecordInboxReader
from pipeline_monitor.linker import annotate_stages
from pipeline_monitor.mqtt_tail import MqttTail, collapse_heartbeats
from pipeline_monitor.oracle_reader import OracleRecentReader
from pipeline_monitor.rollup import merged_children

HEARTBEAT_TIMEOUT_S = 60.0
VERIFY_BATCH = 1   # 1ループtickあたりに検証を試みる未検証行の数(ブロック時間を短く保つ)
INBOX_LIMIT = 100  # ③④が見る直近アクティビティの件数(受信順)


@dataclass
class Deps:
    tail: MqttTail
    inbox: RecordInboxReader
    oracle: OracleRecentReader
    oracle_query_loader: Callable[[], object]     # -> OracleQuery
    password_getter: Callable[[], str]
    ssid_getter: Callable[[], str]
    expected_ssid: str


def _safe(fn, default):
    try:
        return fn(), None
    except Exception as e:  # noqa: BLE001  ペインは落とさない
        return default, f"{type(e).__name__}: {e}"


def _addstr(win, y, x, text, attr=0):
    h, w = win.getmaxyx()
    if 0 <= y < h and 0 <= x < w:
        win.addnstr(y, x, text, max(0, w - x - 1), attr)


def _draw_pane(parent, height, width, y, x, fn, *args):
    """1ペインを独立に描画する。derwin生成や fn の例外を捕まえ、他ペインへ
    波及させない（§7 各ペイン独立）。失敗したペインには理由を1行出す。"""
    try:
        win = parent.derwin(height, width, y, x)
    except curses.error:
        return  # このフレームでは描けないだけ。次フレームで再試行。
    try:
        fn(win, *args)
    except Exception as e:  # noqa: BLE001  ペインは落とさない
        try:
            win.erase()
            win.box()
            _addstr(win, 1, 2, f"⚠ 描画失敗: {type(e).__name__}: {e}")
            win.noutrefresh()
        except curses.error:
            pass


def _draw_children(win, inbox_view, inbox_err, msgs, now):
    win.erase()
    win.box()
    _addstr(win, 0, 2, " ①子Pi別 受信 ", curses.A_BOLD)
    # 「どの子から何件」は DB(record_inbox) が真実。MQTT で liveness を重ねる。
    devices = inbox_view.devices if inbox_view else []
    stats = merged_children(devices, msgs, now=now, heartbeat_timeout=HEARTBEAT_TIMEOUT_S)
    if inbox_err:
        _addstr(win, 1, 2, f"⚠ DB取得失敗(件数はMQTT分のみ): {inbox_err}")
        start = 2
    else:
        start = 1
    _addstr(win, start, 2, f"{'device':<12}{'rec':>4} {'状態':<8}{'hb':>6} 最新mk")
    for i, s in enumerate(stats, start=start + 1):
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
    lines = collapse_heartbeats(msgs)
    visible = lines[-(h - 2):] if len(lines) > h - 2 else lines
    for i, ln in enumerate(visible, start=1):
        ts = time.strftime("%H:%M:%S", time.localtime(ln.ts))
        # heartbeatのまとめ行は薄く表示し、record/status/ack を目立たせる。
        _addstr(win, i, 2, f"{ts} {ln.text}", curses.A_DIM if ln.dim else 0)
    win.noutrefresh()


def _draw_inbox(win, view, err, msgs):
    win.erase()
    win.box()
    _addstr(win, 0, 2, " ③MQTT→Oracle (record_inbox) ", curses.A_BOLD)
    if err or view is None:
        _addstr(win, 1, 2, f"⚠ 取得失敗: {err}")
        win.noutrefresh()
        return
    linked = annotate_stages(view.rows, {m.event_id for m in msgs if m.event_id})
    _addstr(win, 1, 2,
            f"received(滞留)={view.received}  sent={view.sent}  "
            f"failed(諦め)={view.failed}  合計={view.total}",
            curses.A_BOLD)
    h, _ = win.getmaxyx()
    for i, lk in enumerate(linked[: h - 3], start=2):
        r = lk.inbox
        err_s = f" !{r.last_error[:18]}" if r.last_error else ""
        _addstr(win, i, 2,
                f"{(r.device_id or '?'):<8} {r.event_id[:10]:<10} {lk.stage:<18} "
                f"r{r.retry_count}{err_s}")
    win.noutrefresh()


def _fmt_mk(mk: str | None) -> str:
    if mk and len(mk) == 14 and mk.isdigit():
        return f"{mk[0:4]}-{mk[4:6]}-{mk[6:8]} {mk[8:10]}:{mk[10:12]}:{mk[12:14]}"
    return mk or "-"


def _fmt_sent_at(sent_at_iso: str | None) -> str:
    """送信(Oracle受理)時刻をローカル時刻で表示する。mk_date(発生時刻)とは
    別物であることを画面上でも区別できるようにするため。"""
    if not sent_at_iso:
        return "-"
    try:
        dt = datetime.fromisoformat(sent_at_iso).astimezone()
    except ValueError:
        return sent_at_iso[:16]
    return dt.strftime("%m/%d %H:%M")


def _draw_uploaded(win, view, err, verified: dict[str, bool], ssid, expected_ssid):
    win.erase()
    win.box()
    _addstr(win, 0, 2, " ④アップロード履歴 ([r]再検証) ", curses.A_BOLD)
    if err or view is None:
        _addstr(win, 1, 2, f"⚠ 取得失敗: {err}")
        win.noutrefresh()
        return
    sent_rows = sorted(
        (r for r in view.rows if r.status == "sent"),
        key=lambda r: r.sent_at_iso or "", reverse=True,
    )
    note = "" if ssid == expected_ssid else f"  (確認はSSID一致時のみ実行: 現在={ssid})"
    _addstr(win, 1, 2, f"送信済み={len(sent_rows)}件{note}", curses.A_BOLD)
    _addstr(win, 2, 2, f"{'発生(mk_date)':<19} {'送信時刻':<11} T1  STA(1/2/3)")
    h, _ = win.getmaxyx()
    for i, r in enumerate(sent_rows[: h - 4], start=3):
        v = verified.get(r.event_id)
        mark = "✓確認" if v is True else ("⚠不一致" if v is False else "…未確認")
        # T1_STATUS は入退室に限らないため生の数値を表示する。局番は子Piが
        # 送ってきたそのままの値(固定フィルタで先読みしない)。発生時刻(mk_date)
        # と送信(Oracle受理)時刻は別物なので両方出す(過去の送信を"今送った"と
        # 誤読しないように)。
        _addstr(win, i, 2,
                f"{_fmt_mk(r.mk_date_committed):<19} {_fmt_sent_at(r.sent_at_iso):<11} "
                f"T1={r.t1_status:<3} {r.sta_no1}/{r.sta_no2}/{r.sta_no3:<10} {mark}")
    win.noutrefresh()


def run(stdscr, deps: Deps) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    deps.tail.start()

    # I/O（record_inbox 読取・Oracle 照会・SSID 取得）は別スレッドで回し、描画
    # ループは決してブロックしない。工場網未接続で Oracle 照会が数十秒かかっても
    # 画面と [q] が固まらない（§7 の堅牢性を実運用まで担保する）。
    lock = threading.Lock()
    state: dict = {
        "inbox_view": None, "inbox_err": None,
        "verified": {},                 # event_id -> True(確認済)/False(不一致)
        "ssid": "(取得中)",
    }
    stop_event = threading.Event()
    oracle_now = threading.Event()      # [r] 押下で verified キャッシュを全消去→再検証

    def _verify_row(row):
        base = deps.oracle_query_loader()
        mk = row.mk_date_committed or row.mk_date
        q = replace(
            base, sta_no1=row.sta_no1, sta_no2=row.sta_no2, sta_no3=row.sta_no3,
            mk_date_from=mk, mk_date_to=mk, limit=1,
        )
        return deps.oracle.verify_exact(q, deps.password_getter())

    def worker():
        while not stop_event.is_set():
            inbox_view, inbox_err = _safe(lambda: deps.inbox.read(limit=INBOX_LIMIT), None)
            ssid, _ = _safe(deps.ssid_getter, "(不明)")
            with lock:
                state["inbox_view"], state["inbox_err"] = inbox_view, inbox_err
                state["ssid"] = ssid

            if oracle_now.is_set():
                with lock:
                    state["verified"] = {}
                oracle_now.clear()

            # 局番はここでは先読みしない。SSID一致時のみ、行自身の
            # sta_no1-3/mk_date_committed で Oracle への実在を確認する。
            if ssid == deps.expected_ssid and inbox_view is not None:
                with lock:
                    verified = dict(state["verified"])
                pending = [
                    r for r in inbox_view.rows
                    if r.status == "sent" and r.event_id not in verified
                ]
                for row in pending[:VERIFY_BATCH]:
                    result, verr = _safe(lambda row=row: _verify_row(row), None)
                    if verr is None and result is not None:
                        found = bool(result.ok and result.rows)
                        with lock:
                            state["verified"][row.event_id] = found
            stop_event.wait(1.0)        # inbox/SSID/検証 の更新間隔（UIスレッド外）

    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()

    try:
        while True:
            now = time.time()
            msgs = deps.tail.messages()
            with lock:
                inbox_view = state["inbox_view"]
                inbox_err = state["inbox_err"]
                verified = dict(state["verified"])
                ssid = state["ssid"]
            h, w = stdscr.getmaxyx()
            mid_y, mid_x = h // 2, w // 2
            stdscr.erase()
            _addstr(stdscr, 0, 0,
                    " presence パイプライン監視  [r]再検証 [q]終了 ", curses.A_REVERSE)
            # 端末が小さすぎて4分割できないときは、落とさず一言だけ出す。
            if mid_y - 1 < 3 or h - mid_y - 1 < 3 or mid_x < 12 or w - mid_x < 12:
                _addstr(stdscr, 2, 0, "端末が小さすぎます。ウィンドウを広げてください。")
            else:
                # 各ペインを独立に描画。1ペインの例外は他ペインへ波及させない（§7）。
                _draw_pane(stdscr, mid_y - 1, mid_x, 1, 0,
                           _draw_children, inbox_view, inbox_err, msgs, now)
                _draw_pane(stdscr, mid_y - 1, w - mid_x, 1, mid_x,
                           _draw_mqtt, msgs)
                _draw_pane(stdscr, h - mid_y - 1, mid_x, mid_y, 0,
                           _draw_inbox, inbox_view, inbox_err, msgs)
                _draw_pane(stdscr, h - mid_y - 1, w - mid_x, mid_y, mid_x,
                           _draw_uploaded, inbox_view, inbox_err, verified, ssid,
                           deps.expected_ssid)
            stdscr.noutrefresh()
            curses.doupdate()

            ch = stdscr.getch()
            if ch in (ord("q"), ord("Q")):
                break
            if ch in (ord("r"), ord("R")):
                oracle_now.set()
            time.sleep(0.3)
    finally:
        stop_event.set()
        oracle_now.set()
        worker_thread.join(timeout=3.0)
        deps.tail.stop()
