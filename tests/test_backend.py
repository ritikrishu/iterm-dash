import asyncio
from types import SimpleNamespace

import iterm2
import pytest
from iterm2 import api_pb2
from screens import make_buffer, make_screen
from test_palette import FakeProfile

import iterm_dash.backend as backend_mod
from iterm_dash.backend import ItermBackend, read_status
from iterm_dash.palette import DEFAULT_PALETTE, Palette
from iterm_dash.registry import SessionInfo
from iterm_dash.render import screen_rows
from iterm_dash.smoke import format_line

# Synthetic screens only; never real terminal content.
SCREEN = [[("h", {}), ("i", {"bold": True})], [("$", {"fgStandard": 2}), (" ", {"bgStandard": 4})]]
PROFILE = FakeProfile(True, fg_dark=(10, 20, 30), bg_dark=(1, 2, 3), fg_light=(200, 200, 200), bg_light=(250, 250, 250))
DARK = Palette.from_profile(PROFILE, dark=True)
LIGHT = Palette.from_profile(PROFILE, dark=False)


def lines_screen(n, changed=None):
    """n synthetic styled lines; line ``changed`` (if any) carries different text."""
    return [
        [(("X" if i == changed else "a"), {"fgStandard": i % 16}), ("b", {"bold": True})] for i in range(n)
    ]


def numbered(start, n):
    """n distinct synthetic lines numbered from ``start`` (start+1 is the same screen scrolled by one)."""
    return [[(c, {"fgStandard": k % 16}) for c in f"line {k:03d}"] for k in range(start, start + n)]


class FakeHub:
    def __init__(self):
        self.sessions = []  # each call: list of SessionInfo
        self.frames = {}  # sid -> [ScreenFrame]
        self.statuses = {}  # sid -> [StatusView]
        self.changed_ats = {}  # sid -> [changed_at], parallel to statuses

    def ids(self):
        return [s.id for s in self.sessions[-1]] if self.sessions else None

    async def set_sessions(self, sessions):
        self.sessions.append(list(sessions))

    async def set_screen(self, sid, frame):
        self.frames.setdefault(sid, []).append(frame)

    async def set_status(self, sid, view, changed_at):
        self.statuses.setdefault(sid, []).append(view)
        self.changed_ats.setdefault(sid, []).append(changed_at)


class FakeStreamer:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        self.session.open_streamers += 1
        return self

    async def __aexit__(self, *exc):
        self.session.open_streamers -= 1
        if self.session.connection.closed.is_set():
            # Like real iterm2: unsubscribing sends on the dead websocket and raises.
            raise ConnectionError("send on closed socket")

    async def async_get(self):
        await asyncio.Event().wait()  # no screen change ever arrives


class FakeSession:
    """Read-only session double: it has no write methods at all."""

    def __init__(self, sid, job="2.1.310", command_line="claude --dangerously-skip-permissions"):
        self.session_id = sid
        self.connection = None  # set by FakeConnection.attach
        self.grid_size = SimpleNamespace(width=214, height=len(SCREEN))  # matches its buffer
        self.variables = {"autoName": f"title {sid}", "jobName": job, "commandLine": command_line}
        self.buffer = make_buffer(SCREEN)
        self.profile = PROFILE
        self.profile_calls = 0
        self.profile_error = None
        self.screen_error = None
        self.open_streamers = 0
        self.calls = []

    def reply(self):
        """Answer a get-buffer RPC the way iTerm2 does."""
        if self.screen_error is not None:
            raise self.screen_error
        msg = api_pb2.ServerOriginatedMessage()
        msg.get_buffer_response.CopyFrom(self.buffer)
        return msg

    async def async_get_variable(self, name):
        return self.variables.get(name)

    async def async_get_profile(self):
        self.profile_calls += 1
        if self.profile_error is not None:
            raise self.profile_error
        return self.profile

    def get_screen_streamer(self, want_contents=True):
        assert want_contents is False
        return FakeStreamer(self)

    async def async_activate(self, select_tab=True, order_window_front=True):
        self.calls.append(("session", self.session_id, select_tab, order_window_front))


class FakeApp:
    def __init__(self, sessions, theme="dark"):
        self.sessions = list(sessions)
        self.theme = theme
        self.calls = []
        self.layout = None  # windows -> tabs -> session ids; None means one window, one tab
        self.refreshes = 0
        self.on_refresh = None  # what reloading the hierarchy changes, if anything
        for s in self.sessions:
            s.calls = self.calls

    @property
    def terminal_windows(self):
        if self.layout is None:
            return [SimpleNamespace(tabs=[SimpleNamespace(sessions=list(self.sessions))])]
        by_id = {s.session_id: s for s in self.sessions}
        return [
            SimpleNamespace(tabs=[SimpleNamespace(sessions=[by_id[i] for i in tab]) for tab in window])
            for window in self.layout
        ]

    def get_session_by_id(self, sid, include_buried=True):
        return next((s for s in self.sessions if s.session_id == sid), None)

    async def async_get_variable(self, name):
        assert name == "effectiveTheme"
        return self.theme

    async def async_activate(self, raise_all_windows=True, ignoring_other_apps=False):
        self.calls.append(("app", raise_all_windows, ignoring_other_apps))

    async def async_refresh(self):
        """Reload the hierarchy: a read, like iterm2's App.async_refresh."""
        self.refreshes += 1
        if self.on_refresh is not None:
            self.on_refresh()


class FakeConnection:
    def __init__(self):
        self.closed = asyncio.Event()
        self.websocket = SimpleNamespace(wait_closed=self.closed.wait)
        self.sessions = {}

    def attach(self, sessions):
        for s in sessions:
            s.connection = self
            self.sessions[s.session_id] = s
        return self


@pytest.fixture(autouse=True)
def buffer_rpc(monkeypatch):
    """Route iterm2's get-buffer RPC to the fake session (never real terminal content)."""

    async def get_buffer(connection, session_id, windowed_coord_range=None, style=False):
        assert windowed_coord_range is None and style is True
        return connection.sessions[session_id].reply()

    monkeypatch.setattr(iterm2.rpc, "async_get_screen_contents", get_buffer)


@pytest.fixture
def renders(monkeypatch):
    """Counts line renders done by the backend."""
    calls = []
    real = backend_mod.line_row

    def counting(line, palette):
        calls.append(1)
        return real(line, palette)

    monkeypatch.setattr(backend_mod, "line_row", counting)
    return calls


@pytest.fixture
def live(monkeypatch):
    """Fake lifecycle/theme monitors and native status, patched into the iterm2 module."""
    monitors = {}
    statuses = {}

    def monitor_class(kind):
        class Monitor:
            def __init__(self, connection, *args):
                self.connection = connection
                self.args = args
                self.queue = asyncio.Queue()
                monitors[kind] = self

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                if self.connection.closed.is_set():
                    raise ConnectionError("send on closed socket")  # unsubscribe on a dead socket

            async def async_get(self):
                return await self.queue.get()

        return Monitor

    for kind in ("LayoutChangeMonitor", "NewSessionMonitor", "SessionTerminationMonitor", "VariableMonitor"):
        monkeypatch.setattr(iterm2, kind, monitor_class(kind))

    async def invoke(connection, invocation, timeout=-1):
        assert invocation.startswith("iterm2.get_session_status(") and timeout == 2
        value = statuses.get(invocation.split('"')[1], {"status": None})
        if isinstance(value, Exception):  # the read itself fails (e.g. a timeout)
            raise value
        return value

    monkeypatch.setattr(iterm2, "async_invoke_function", invoke)
    return SimpleNamespace(monitors=monitors, statuses=statuses)


async def until(pred, timeout=2.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.005)


def start(sessions):
    app, conn, hub = FakeApp(sessions), FakeConnection().attach(sessions), FakeHub()
    backend = ItermBackend(conn, hub, preview_interval=0.01, status_interval=0.01, app=app)
    return backend, app, conn, hub


def unit_backend(s, theme="dark"):
    return ItermBackend(FakeConnection().attach([s]), FakeHub(), app=FakeApp([s], theme=theme))


async def stop(task, conn):
    """Close the connection; run() must return on its own and leave no task behind."""
    conn.closed.set()
    done, _ = await asyncio.wait({task}, timeout=2)
    if not done:
        await _force_cancel_all()  # so a regression fails here instead of hanging asyncio.run
        raise AssertionError("run() did not return after the connection closed")
    task.result()
    assert asyncio.all_tasks() == {asyncio.current_task()}


async def _force_cancel_all():
    for _ in range(100):
        others = asyncio.all_tasks() - {asyncio.current_task()}
        if not others:
            return
        for t in others:
            t.cancel()
        await asyncio.wait(others, timeout=0.05)


# -- brief tests -------------------------------------------------------------


def test_fetch_frame_returns_none_when_session_gone():
    s = FakeSession("A")
    backend = unit_backend(s)
    s.screen_error = iterm2.RPCException("SESSION_NOT_FOUND")
    assert asyncio.run(backend.fetch_frame(s)) is None
    s.screen_error = None
    s.buffer.status = api_pb2.GetBufferResponse.Status.Value("SESSION_NOT_FOUND")
    assert asyncio.run(backend.fetch_frame(s)) is None


def test_read_status_returns_none_only_when_the_read_fails(monkeypatch):
    async def boom(connection, invocation, timeout=-1):
        raise iterm2.RPCException("Timeout")

    monkeypatch.setattr(iterm2, "async_invoke_function", boom)
    assert asyncio.run(read_status(object(), "A")) is None

    async def not_a_dict(connection, invocation, timeout=-1):
        return None  # iTerm2 answered, but with no status at all

    monkeypatch.setattr(iterm2, "async_invoke_function", not_a_dict)
    assert asyncio.run(read_status(object(), "A")) == {}


def test_read_status_calls_native_status_function(monkeypatch):
    seen = []

    async def invoke(connection, invocation, timeout=-1):
        seen.append((invocation, timeout))
        return {"status": "working"}

    monkeypatch.setattr(iterm2, "async_invoke_function", invoke)
    assert asyncio.run(read_status(object(), "ABC-1")) == {"status": "working"}
    assert seen == [('iterm2.get_session_status(session_id: "ABC-1")', 2)]


def test_fetch_frame_builds_frame_from_screen():
    s = FakeSession("A")
    frame = asyncio.run(unit_backend(s, theme="dark").fetch_frame(s))
    assert (frame.cols, frame.rows) == (214, len(SCREEN))
    assert (frame.fg, frame.bg) == (DARK.fg, DARK.bg) == ("#0a141e", "#010203")
    assert frame.lines == screen_rows(make_screen(SCREEN), DARK)
    assert frame.ts > 0


def test_frame_rows_come_from_the_buffer_not_the_cached_grid_size():
    s = FakeSession("A")
    s.grid_size = SimpleNamespace(width=214, height=57)  # stale: no layout event since the screen grew
    s.buffer = make_buffer(numbered(0, 60))
    frame = asyncio.run(unit_backend(s).fetch_frame(s))
    assert (frame.cols, frame.rows) == (214, 60)
    assert frame.lines == screen_rows(make_screen(numbered(0, 60)), DARK)  # every line, none cut off


def test_failed_profile_read_uses_default_palette_without_caching_it():
    async def main():
        s = FakeSession("A")
        backend = unit_backend(s)
        s.profile_error = iterm2.RPCException("Timeout")
        first = await backend.fetch_frame(s)
        assert (first.fg, first.bg) == (DEFAULT_PALETTE.fg, DEFAULT_PALETTE.bg)
        s.profile_error = None
        second = await backend.fetch_frame(s)
        assert (second.fg, second.bg) == (DARK.fg, DARK.bg)  # retried
        assert second.lines == screen_rows(make_screen(SCREEN), DARK)
        await backend.fetch_frame(s)
        assert s.profile_calls == 2  # the real palette is cached

    asyncio.run(main())


# -- per-line render cache -----------------------------------------------------


def test_unchanged_lines_are_not_rerendered(renders):
    async def main():
        s = FakeSession("A")
        backend = unit_backend(s)
        s.buffer = make_buffer(lines_screen(5))
        first = await backend.fetch_frame(s)
        assert len(renders) == 5
        s.buffer = make_buffer(lines_screen(5, changed=3))
        second = await backend.fetch_frame(s)
        assert len(renders) == 6  # only the changed line
        assert second.lines == screen_rows(make_screen(lines_screen(5, changed=3)), DARK)
        assert first.lines == screen_rows(make_screen(lines_screen(5)), DARK)  # earlier frame untouched
        s.buffer = make_buffer(lines_screen(5))
        third = await backend.fetch_frame(s)
        assert len(renders) == 7  # line 3 left the screen last frame, so the cache had dropped it
        assert third.lines == screen_rows(make_screen(lines_screen(5)), DARK)
        s.buffer = make_buffer(lines_screen(6))
        fourth = await backend.fetch_frame(s)
        assert len(renders) == 8  # a longer screen: only the new line renders
        assert fourth.lines == screen_rows(make_screen(lines_screen(6)), DARK)

    asyncio.run(main())


def test_scrolled_screen_renders_only_new_line(renders):
    async def main():
        s = FakeSession("A")
        backend = unit_backend(s)
        s.buffer = make_buffer(numbered(0, 8))
        await backend.fetch_frame(s)
        assert len(renders) == 8
        s.buffer = make_buffer(numbered(1, 8))  # shifted up by one, one new bottom line
        frame = await backend.fetch_frame(s)
        assert len(renders) == 9
        assert frame.lines == screen_rows(make_screen(numbered(1, 8)), DARK)

    asyncio.run(main())


def test_identical_lines_render_once(renders):
    blank = [(" ", {})]
    screen = [blank, [("x", {})], blank, blank]
    s = FakeSession("A")
    s.buffer = make_buffer(screen)
    frame = asyncio.run(unit_backend(s).fetch_frame(s))
    assert len(renders) == 2
    assert frame.lines == screen_rows(make_screen(screen), DARK)


def test_smoke_line_has_metadata_only():
    info = SessionInfo("ABCDEF12-3456-7890-ABCD-EF1234567890", "Secret project", "2.1.310", True, 214, 57)
    line = format_line(info, {"status": "idle", "detail": "private text"}, 1.2, 40)
    for part in ("ABCDEF12", "214x57", "idle", "1.2ms"):
        assert part in line
    assert "private text" not in line


# -- run loop ----------------------------------------------------------------


def test_run_returns_when_connection_closes_and_leaves_no_tasks(live):
    async def main():
        a, b = FakeSession("A"), FakeSession("B", job="zsh", command_line="-zsh")
        live.statuses["A"] = {"status": "working", "detail": "x"}
        backend, app, conn, hub = start([a, b])
        task = asyncio.create_task(backend.run())
        await until(lambda: a.open_streamers == b.open_streamers == 1 and "A" in hub.frames and "B" in hub.frames)
        await until(lambda: "A" in hub.statuses and "B" in hub.statuses)
        # Per-session tasks are now blocked inside the streamer's async_get(), and every
        # streamer/monitor __aexit__ will raise (unsubscribe on the dead socket).
        await stop(task, conn)
        assert a.open_streamers == b.open_streamers == 0
        return hub

    hub = asyncio.run(main())
    assert hub.ids() == ["A", "B"]
    assert [s.is_claude for s in hub.sessions[-1]] == [True, False]
    assert hub.statuses["A"][-1].tone == "working"
    assert hub.statuses["B"][-1].tone == "shell"
    assert hub.frames["A"][0].lines == screen_rows(make_screen(SCREEN), DARK)


def test_vanished_session_is_cancelled_and_forgotten(live, renders):
    async def main():
        a, b = FakeSession("A"), FakeSession("B")
        live.statuses["B"] = {"status": "idle"}
        backend, app, conn, hub = start([a, b])
        task = asyncio.create_task(backend.run())
        await until(lambda: b.open_streamers == 1 and len(hub.statuses.get("B", [])) == 1)

        app.sessions.remove(b)
        live.monitors["SessionTerminationMonitor"].queue.put_nowait("B")
        await until(lambda: hub.ids() == ["A"])
        assert b.open_streamers == 0  # its task was cancelled before the list went out
        frames_after = len(hub.frames["B"])
        await asyncio.sleep(0.05)
        assert len(hub.frames["B"]) == frames_after
        assert len(hub.statuses["B"]) == 1

        # Same status again after it returns: pushed again, so the tracker forgot it.
        renders.clear()
        app.sessions.append(b)
        live.monitors["NewSessionMonitor"].queue.put_nowait("B")
        await until(lambda: len(hub.statuses["B"]) == 2 and b.open_streamers == 1)
        assert hub.statuses["B"][-1].tone == "done"
        await asyncio.sleep(0.05)
        assert len(renders) == len(SCREEN)  # its line cache was dropped: one full render
        await stop(task, conn)

    asyncio.run(main())


def test_exclude_drops_a_session(live):
    async def main():
        a, b = FakeSession("A"), FakeSession("B")
        backend, app, conn, hub = start([a, b])
        task = asyncio.create_task(backend.run())
        await until(lambda: hub.ids() == ["A", "B"] and b.open_streamers == 1)
        backend.exclude("B")
        await until(lambda: hub.ids() == ["A"])
        assert b.open_streamers == 0
        await stop(task, conn)

    asyncio.run(main())


def test_layout_change_refreshes_palette_and_rerenders_every_line(live, renders):
    async def main():
        a = FakeSession("A")
        a.buffer = make_buffer(lines_screen(5))
        backend, app, conn, hub = start([a])
        task = asyncio.create_task(backend.run())
        await until(lambda: "A" in hub.frames)
        await asyncio.sleep(0.05)
        assert a.profile_calls == 1  # cached between fetches
        renders.clear()
        new_profile = FakeProfile(False, fg=(255, 0, 0), bg=(0, 0, 255))
        a.profile = new_profile
        live.monitors["LayoutChangeMonitor"].queue.put_nowait(None)
        await until(lambda: hub.frames["A"][-1].fg == "#ff0000")
        assert hub.frames["A"][-1].bg == "#0000ff"
        await asyncio.sleep(0.05)
        assert len(renders) == 5  # palette changed: every line re-rendered, once
        new = Palette.from_profile(new_profile, dark=True)
        assert hub.frames["A"][-1].lines == screen_rows(make_screen(lines_screen(5)), new)
        await stop(task, conn)

    asyncio.run(main())


def test_theme_change_rerenders_with_new_colours(live, renders):
    async def main():
        a = FakeSession("A")
        a.buffer = make_buffer(lines_screen(5))
        backend, app, conn, hub = start([a])
        task = asyncio.create_task(backend.run())
        await until(lambda: "A" in hub.frames and "VariableMonitor" in live.monitors)
        await asyncio.sleep(0.05)
        assert live.monitors["VariableMonitor"].args == (iterm2.VariableScopes.APP, "effectiveTheme", None)
        assert hub.frames["A"][-1].fg == DARK.fg
        renders.clear()
        app.theme = "light"  # macOS switched; no layout change happens
        live.monitors["VariableMonitor"].queue.put_nowait("light")
        await until(lambda: hub.frames["A"][-1].fg == LIGHT.fg)
        assert hub.frames["A"][-1].bg == LIGHT.bg
        await asyncio.sleep(0.05)
        assert len(renders) == 5  # every line re-rendered with the new colours, once
        assert hub.frames["A"][-1].lines == screen_rows(make_screen(lines_screen(5)), LIGHT)
        await stop(task, conn)

    asyncio.run(main())


def test_status_appearing_on_a_shell_rediscovers_it_as_claude(live):
    async def main():
        c = FakeSession("C", job="zsh", command_line="-zsh")
        backend, app, conn, hub = start([c])
        task = asyncio.create_task(backend.run())
        await until(lambda: hub.sessions and not hub.sessions[-1][0].is_claude)
        live.statuses["C"] = {"status": "working"}
        await until(lambda: hub.sessions[-1][0].is_claude)
        await until(lambda: hub.statuses["C"][-1].tone == "working")
        await stop(task, conn)

    asyncio.run(main())


def test_new_detail_or_colour_under_the_same_status_is_pushed(live):
    async def main():
        a = FakeSession("A")
        live.statuses["A"] = {"status": "working", "detail": "1 background task running"}
        backend, app, conn, hub = start([a])
        task = asyncio.create_task(backend.run())
        await until(lambda: len(hub.statuses.get("A", [])) == 1)
        await asyncio.sleep(0.05)
        assert len(hub.statuses["A"]) == 1  # an unchanged view is not pushed again
        live.statuses["A"] = {"status": "working", "detail": "2 background tasks running"}
        await until(lambda: len(hub.statuses["A"]) == 2)
        live.statuses["A"] = {"status": "working", "detail": "2 background tasks running", "dot_color": "#123456"}
        await until(lambda: len(hub.statuses["A"]) == 3)
        await stop(task, conn)
        return hub

    hub = asyncio.run(main())
    views = hub.statuses["A"]
    assert [v.tone for v in views] == ["working"] * 3
    assert [v.detail for v in views] == ["1 background task running", "2 background tasks running", "2 background tasks running"]
    assert [v.color for v in views] == ["#ff9500", "#ff9500", "#123456"]
    times = hub.changed_ats["A"]
    assert times[1] == times[2] == times[0]  # same tone and label: changed_at is kept


def test_failed_status_read_keeps_the_last_view_and_does_not_rediscover(live):
    async def main():
        a = FakeSession("A", job="node", command_line="node server.js")  # Claude only by its status
        live.statuses["A"] = {"status": "waiting", "detail": "Which index?"}
        backend, app, conn, hub = start([a])
        task = asyncio.create_task(backend.run())
        await until(lambda: hub.sessions and hub.sessions[-1][0].is_claude and len(hub.statuses.get("A", [])) >= 1)
        await asyncio.sleep(0.05)
        passes, pushes = len(hub.sessions), len(hub.statuses["A"])
        live.statuses["A"] = iterm2.RPCException("Timeout")
        await asyncio.sleep(0.15)  # many status-loop rounds, every read failing
        assert len(hub.statuses["A"]) == pushes  # no push, so no "Status unknown"
        assert hub.statuses["A"][-1].tone == "waiting"
        assert len(hub.sessions) == passes  # a failed read is not a status flip
        live.statuses["A"] = {"status": "waiting", "detail": "Which index?"}
        await asyncio.sleep(0.05)
        assert len(hub.statuses["A"]) == pushes and len(hub.sessions) == passes
        await stop(task, conn)

    asyncio.run(main())


def test_cells_keep_first_seen_order_and_new_sessions_go_last(live):
    async def main():
        a, b, c = FakeSession("A"), FakeSession("B"), FakeSession("C")
        backend, app, conn, hub = start([a, b, c])
        app.layout = [[["A"], ["B"]], [["C"]]]  # two windows
        task = asyncio.create_task(backend.run())
        await until(lambda: hub.ids() == ["A", "B", "C"])

        async def relayout(layout, *new):
            conn.attach(new)
            app.sessions.extend(new)
            app.layout = layout
            passes = len(hub.sessions)
            live.monitors["LayoutChangeMonitor"].queue.put_nowait(None)
            await until(lambda: len(hub.sessions) > passes)
            return hub.ids()

        # A new tab in window 1 sits before C in iTerm2's order, but its cell goes last.
        assert await relayout([[["A"], ["D"], ["B"]], [["C"]]], FakeSession("D")) == ["A", "B", "C", "D"]
        # Moving and reordering existing tabs, across windows, changes nothing.
        assert await relayout([[["C"], ["B"], ["A"]], [["D"]]]) == ["A", "B", "C", "D"]
        # Two new sessions in one pass are appended in iTerm2's order among themselves.
        layout = [[["F"], ["C"], ["B"], ["A"]], [["D"], ["E"]]]
        assert await relayout(layout, FakeSession("F"), FakeSession("E")) == ["A", "B", "C", "D", "F", "E"]
        # A closed session drops out and the rest close the gap.
        app.sessions.remove(b)
        assert await relayout([[["F"], ["C"], ["A"]], [["D"], ["E"]]]) == ["A", "C", "D", "F", "E"]
        await stop(task, conn)

    asyncio.run(main())


async def refetch(live, hub, sid):
    """Make every pump fetch again (a theme event) and wait for the new frame."""
    n = len(hub.frames[sid])
    live.monitors["VariableMonitor"].queue.put_nowait("dark")
    await until(lambda: len(hub.frames[sid]) > n)


def test_buffer_size_mismatch_rediscovers_with_a_refreshed_app(live):
    async def main():
        a = FakeSession("A")
        a.buffer = make_buffer(numbered(0, 60))  # the screen grew; the cached grid_size says 57
        a.grid_size = SimpleNamespace(width=214, height=57)
        backend, app, conn, hub = start([a])
        app.on_refresh = lambda: setattr(a, "grid_size", SimpleNamespace(width=214, height=60))
        task = asyncio.create_task(backend.run())
        await until(lambda: hub.sessions and hub.sessions[-1][0].rows == 60)
        assert app.refreshes == 1
        assert hub.frames["A"][-1].rows == 60
        await stop(task, conn)

    asyncio.run(main())


def test_lasting_size_mismatch_rediscovers_only_once(live):
    async def main():
        a = FakeSession("A")
        a.buffer = make_buffer(numbered(0, 60))  # and the refreshed App keeps saying 57
        a.grid_size = SimpleNamespace(width=214, height=57)
        backend, app, conn, hub = start([a])
        task = asyncio.create_task(backend.run())
        await until(lambda: app.refreshes == 1 and len(hub.sessions) == 2 and "VariableMonitor" in live.monitors)
        for _ in range(3):
            await refetch(live, hub, "A")
        await asyncio.sleep(0.1)
        assert app.refreshes == 1 and len(hub.sessions) == 2  # no pass per frame
        a.buffer = make_buffer(numbered(0, 62))  # a new mismatch is worth one more pass
        await refetch(live, hub, "A")
        await until(lambda: app.refreshes == 2 and len(hub.sessions) == 3)
        await stop(task, conn)

    asyncio.run(main())


def logged_triggers(caplog):
    """Trigger names from the backend's per-pass log lines ("discovery a,b: sessions=…")."""
    names = set()
    for r in caplog.records:
        msg = r.getMessage()
        if r.name == "iterm_dash.backend" and msg.startswith("discovery "):
            names.update(msg.split(":", 1)[0].removeprefix("discovery ").split(","))
    return names


def test_periodic_discovery_picks_up_a_title_change_without_any_event(live, caplog):
    async def main():
        a = FakeSession("A")
        app, conn, hub = FakeApp([a]), FakeConnection().attach([a]), FakeHub()
        backend = ItermBackend(
            conn, hub, preview_interval=0.01, status_interval=0.01, discovery_interval=0.05, app=app
        )
        task = asyncio.create_task(backend.run())
        await until(lambda: hub.sessions and hub.sessions[-1][0].title == "title A")
        a.variables["autoName"] = "renamed by Claude"  # iTerm2 sends no monitor event for this
        await until(lambda: hub.sessions[-1][0].title == "renamed by Claude")
        assert app.refreshes >= 1  # a periodic pass reloads the App too, for missed layout events
        await stop(task, conn)

    with caplog.at_level("INFO", logger="iterm_dash.backend"):
        asyncio.run(main())
    assert {"start", "periodic"} <= logged_triggers(caplog)


def test_each_discovery_pass_logs_its_trigger(live, caplog):
    async def main():
        a = FakeSession("A")
        backend, app, conn, hub = start([a])
        task = asyncio.create_task(backend.run())
        await until(lambda: hub.ids() == ["A"] and "VariableMonitor" in live.monitors)

        async def one_pass(cause):
            n = len(hub.sessions)
            cause()
            await until(lambda: len(hub.sessions) > n)
            await asyncio.sleep(0.06)  # past the debounce, so the next cause gets its own pass

        await one_pass(lambda: live.monitors["LayoutChangeMonitor"].queue.put_nowait(None))
        await one_pass(lambda: live.monitors["NewSessionMonitor"].queue.put_nowait("A"))
        await one_pass(lambda: live.monitors["SessionTerminationMonitor"].queue.put_nowait("X"))
        await one_pass(lambda: live.statuses.update(A={"status": "working"}))  # Claude started
        await one_pass(lambda: backend.exclude("X"))
        a.buffer = make_buffer(lines_screen(3))  # the screen changed size without a layout event
        await one_pass(lambda: live.monitors["VariableMonitor"].queue.put_nowait("dark"))
        await stop(task, conn)

    with caplog.at_level("INFO", logger="iterm_dash.backend"):
        asyncio.run(main())
    expected = {"start", "layout", "new", "terminated", "status-flip", "exclude", "size-change"}
    assert expected <= logged_triggers(caplog)
    for r in caplog.records:  # ids and counts only
        assert "title A" not in r.getMessage()


def test_failed_discovery_pass_is_retried(live, monkeypatch):
    monkeypatch.setattr("iterm_dash.backend._RETRY_SECONDS", 0.01)

    async def main():
        a = FakeSession("A")
        backend, app, conn, hub = start([a])
        real = hub.set_sessions
        failures = []

        async def flaky(sessions):
            if not failures:
                failures.append(1)
                raise RuntimeError("boom")
            await real(sessions)

        hub.set_sessions = flaky
        task = asyncio.create_task(backend.run())
        await until(lambda: hub.ids() == ["A"] and a.open_streamers == 1)
        await stop(task, conn)
        return failures

    assert asyncio.run(main()) == [1]


def test_focus_activates_session_then_app(live):
    async def main():
        a, b = FakeSession("A"), FakeSession("B")
        backend, app, conn, hub = start([a, b])
        await backend.focus("B")
        await backend.focus("missing")  # unknown id: no calls, no exception
        return app.calls

    assert asyncio.run(main()) == [("session", "B", True, True), ("app", False, True)]
