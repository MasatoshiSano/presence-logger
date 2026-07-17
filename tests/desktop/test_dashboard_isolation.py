"""_draw_pane のペイン独立性テスト（§7）。

curses を実際に初期化せずに検証するため、必要なメソッドだけ持つ
ダミーウィンドウを使う（dashboard は import 時に端末を要求しない）。
"""
import curses

from pipeline_monitor import dashboard


class FakeWin:
    def __init__(self, h=24, w=80):
        self._h, self._w = h, w
        self.lines: list[str] = []
        self.child: FakeWin | None = None
        self.derwin_raises = False

    def getmaxyx(self):
        return (self._h, self._w)

    def erase(self):
        pass

    def box(self):
        pass

    def addnstr(self, y, x, text, n, attr=0):
        self.lines.append(text[:n])

    def noutrefresh(self):
        pass

    def derwin(self, h, w, y, x):
        if self.derwin_raises:
            raise curses.error("too small")
        self.child = FakeWin(h, w)
        return self.child


def test_draw_pane_catches_fn_exception_and_shows_warning():
    parent = FakeWin()

    def boom(win):
        raise ValueError("kaboom")

    # must not propagate
    dashboard._draw_pane(parent, 10, 40, 1, 0, boom)
    assert parent.child is not None
    assert any("⚠" in line and "kaboom" in line for line in parent.child.lines)


def test_draw_pane_passes_args_through_on_success():
    parent = FakeWin()
    seen = {}

    def ok(win, a, b):
        seen["args"] = (a, b)
        win.addnstr(1, 2, "ok", 10)

    dashboard._draw_pane(parent, 10, 40, 1, 0, ok, "x", "y")
    assert seen["args"] == ("x", "y")
    assert any("⚠" not in line for line in parent.child.lines)


def test_draw_pane_swallows_derwin_error():
    parent = FakeWin()
    parent.derwin_raises = True

    def boom(win):
        raise AssertionError("should not be called")

    # derwin failing (tiny terminal) must not raise or call fn
    dashboard._draw_pane(parent, 1, 1, 1, 0, boom)
    assert parent.child is None
