import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import aiohttp
import iterm2
import pytest
from aiohttp import web

from iterm_dash import cli
from iterm_dash.cli import (
    ServerStartError,
    browser_plugin_installed,
    ensure_server,
    main,
)
from iterm_dash.hub import Hub
from iterm_dash.state import ServerInfo, Settings, read_server_info, write_server_info
from iterm_dash.web import make_app

TOKEN = "cli-test-token"


@pytest.fixture(autouse=True)
def private_state_dir(tmp_path, monkeypatch):
    """Every test gets its own state dir; nothing touches the real one."""
    d = tmp_path / "state"
    monkeypatch.setenv("ITERM_DASH_STATE_DIR", str(d))
    return d


@pytest.fixture(autouse=True)
def forbid_real_spawn(monkeypatch):
    """ensure_server's default spawn is bound at definition time; swap it so no test starts a real server."""
    monkeypatch.setitem(cli.ensure_server.__kwdefaults__, "spawn", no_spawn)


@pytest.fixture
def api_server():
    """The real web app on 127.0.0.1 in a background thread, so sync CLI code can call it."""
    rec = SimpleNamespace(opened=0, stopped=0, on_stop=None, port=None, token=TOKEN)
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    holder = {}

    async def on_focus(_sid):
        pass

    async def on_open():
        rec.opened += 1

    async def on_stop():
        rec.stopped += 1
        if rec.on_stop is not None:
            rec.on_stop()

    async def start():
        app = make_app(Hub(Settings(), lambda _s: None, on_focus), TOKEN, on_open=on_open, on_stop=on_stop)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        holder["runner"] = runner
        rec.port = runner.addresses[0][1]

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start())
        ready.set()
        loop.run_forever()
        loop.run_until_complete(holder["runner"].cleanup())
        loop.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(5)
    yield rec
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)


@pytest.fixture
def doomed():
    """A real process whose pid stands in for the server's; the fake server kills it on stop."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield proc
    if proc.poll() is None:
        proc.kill()
        proc.wait()


def dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def no_spawn(argv, log_path):
    raise AssertionError(f"spawn must not be called: {argv}")


# -- ensure_server ----------------------------------------------------------------


def test_ensure_server_respawns_when_pid_dead(private_state_dir, api_server):
    d = private_state_dir
    d.mkdir(parents=True)
    write_server_info(ServerInfo(dead_pid(), 1, "stale-token", "live"), d)
    fresh = ServerInfo(os.getpid(), api_server.port, TOKEN, "live")
    calls = []

    def spawn(argv, log_path):
        calls.append((argv, log_path))
        write_server_info(fresh, d)

    info = ensure_server("live", d, spawn=spawn, timeout=5.0)

    assert calls == [([sys.executable, "-m", "iterm_dash.server"], d / "server.log")]
    assert info == fresh


def test_ensure_server_reuses_healthy_server(private_state_dir, api_server):
    d = private_state_dir
    d.mkdir(parents=True)
    running = ServerInfo(os.getpid(), api_server.port, TOKEN, "live")
    write_server_info(running, d)

    assert ensure_server("live", d, spawn=no_spawn, timeout=5.0) == running


def test_ensure_server_respawns_when_alive_pid_does_not_answer(private_state_dir, api_server):
    """A live pid with a wrong token (e.g. a recycled pid or port) is not our server."""
    d = private_state_dir
    d.mkdir(parents=True)
    write_server_info(ServerInfo(os.getpid(), api_server.port, "wrong-token", "live"), d)
    fresh = ServerInfo(os.getpid(), api_server.port, TOKEN, "live")
    calls = []

    def spawn(argv, log_path):
        calls.append(argv)
        write_server_info(fresh, d)

    assert ensure_server("live", d, spawn=spawn, timeout=5.0) == fresh
    assert len(calls) == 1


def test_ensure_server_stops_a_server_of_the_other_mode(private_state_dir, api_server, doomed):
    d = private_state_dir
    d.mkdir(parents=True)
    write_server_info(ServerInfo(doomed.pid, api_server.port, TOKEN, "demo"), d)
    api_server.on_stop = lambda: (doomed.kill(), doomed.wait())
    fresh = ServerInfo(os.getpid(), api_server.port, TOKEN, "live")
    calls = []

    def spawn(argv, log_path):
        calls.append(argv)
        write_server_info(fresh, d)

    assert ensure_server("live", d, spawn=spawn, timeout=5.0) == fresh
    assert api_server.stopped == 1
    assert calls == [[sys.executable, "-m", "iterm_dash.server"]]


def test_ensure_server_spawns_demo_with_flag(private_state_dir, api_server):
    d = private_state_dir / "demo"
    d.mkdir(parents=True)
    fresh = ServerInfo(os.getpid(), api_server.port, TOKEN, "demo")
    calls = []

    def spawn(argv, log_path):
        calls.append((argv, log_path))
        write_server_info(fresh, d)

    assert ensure_server("demo", d, spawn=spawn, timeout=5.0) == fresh
    assert calls == [([sys.executable, "-m", "iterm_dash.server", "--demo"], d / "server.log")]


def test_ensure_server_gives_up_after_timeout(private_state_dir):
    d = private_state_dir
    d.mkdir(parents=True)
    calls = []
    with pytest.raises(ServerStartError):
        ensure_server("live", d, spawn=lambda argv, log: calls.append(argv), timeout=0.3)
    assert len(calls) == 1


def test_ensure_server_fails_fast_when_spawned_server_exits(private_state_dir):
    d = private_state_dir
    d.mkdir(parents=True)

    def spawn(argv, log_path):
        return subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])

    start = time.monotonic()
    with pytest.raises(ServerStartError, match="exited during start-up"):
        ensure_server("live", d, spawn=spawn, timeout=10.0)
    assert time.monotonic() - start < 5.0


class FakeProc:
    """A spawned child's handle: records terminate/kill; on_poll lets a test act mid-start."""

    def __init__(self, pid, *, on_poll=None, stubborn=False):
        self.pid = pid
        self.returncode = None
        self.polls = 0
        self.on_poll = on_poll
        self.stubborn = stubborn  # ignores SIGTERM
        self.terminated = False
        self.killed = False

    def poll(self):
        self.polls += 1
        if self.on_poll is not None:
            self.on_poll(self.polls)
        return self.returncode

    def terminate(self):
        self.terminated = True
        if not self.stubborn:
            self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("server", timeout)
        return self.returncode


def test_ensure_server_timeout_terminates_the_spawned_child(private_state_dir):
    d = private_state_dir
    d.mkdir(parents=True)
    child = FakeProc(pid=dead_pid())

    with pytest.raises(ServerStartError, match="didn't start"):
        ensure_server("live", d, spawn=lambda argv, log: child, timeout=0.3)
    assert child.terminated is True
    assert child.killed is False


def test_ensure_server_kills_a_child_that_ignores_terminate(private_state_dir):
    d = private_state_dir
    d.mkdir(parents=True)
    child = FakeProc(pid=dead_pid(), stubborn=True)

    with pytest.raises(ServerStartError):
        ensure_server("live", d, spawn=lambda argv, log: child, timeout=0.3)
    assert child.terminated is True
    assert child.killed is True


def test_ensure_server_adopts_only_its_own_child(private_state_dir, api_server, doomed):
    """A healthy server.json from another start (e.g. an earlier timed-out run) isn't adopted."""
    d = private_state_dir
    d.mkdir(parents=True)
    foreign = ServerInfo(doomed.pid, api_server.port, TOKEN, "live")  # alive and healthy, not ours
    own = ServerInfo(os.getpid(), api_server.port, TOKEN, "live")

    def child_finishes_starting(n):
        if n == 3:
            write_server_info(own, d)

    child = FakeProc(pid=os.getpid(), on_poll=child_finishes_starting)

    def spawn(argv, log_path):
        write_server_info(foreign, d)
        return child

    assert ensure_server("live", d, spawn=spawn, timeout=5.0) == own
    assert child.polls >= 3  # it kept waiting while only the foreign server answered
    assert child.terminated is False


def test_ensure_server_never_adopts_a_foreign_server_and_terminates_on_timeout(private_state_dir, api_server, doomed):
    d = private_state_dir
    d.mkdir(parents=True)
    foreign = ServerInfo(doomed.pid, api_server.port, TOKEN, "live")
    child = FakeProc(pid=os.getpid())

    def spawn(argv, log_path):
        write_server_info(foreign, d)
        return child

    with pytest.raises(ServerStartError):
        ensure_server("live", d, spawn=spawn, timeout=0.3)
    assert child.terminated is True
    assert read_server_info(d) == foreign  # someone else's record is left alone


def test_spawn_detached_uses_new_session_and_logs_to_file(tmp_path, monkeypatch):
    seen = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            seen["argv"] = argv
            seen["kwargs"] = kwargs
            seen["log_inode"] = os.fstat(kwargs["stdout"].fileno()).st_ino

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    log = tmp_path / "server.log"
    assert isinstance(cli.spawn_detached(["python", "-m", "x"], log), FakePopen)

    kw = seen["kwargs"]
    assert seen["argv"] == ["python", "-m", "x"]
    assert kw["start_new_session"] is True
    assert kw["stdout"] is kw["stderr"]
    assert seen["log_inode"] == log.stat().st_ino
    assert kw["stdin"] is subprocess.DEVNULL
    assert kw["cwd"] == tmp_path
    assert log.stat().st_mode & 0o777 == 0o600


# -- iterm-dash stop --------------------------------------------------------------


def test_stop_when_not_running_prints_and_exits_zero(capsys):
    assert main(["stop"]) == 0
    assert "iterm-dash is not running" in capsys.readouterr().out


def test_stop_with_stale_info_clears_it(private_state_dir, capsys):
    d = private_state_dir
    d.mkdir(parents=True)
    write_server_info(ServerInfo(dead_pid(), 1, "stale-token", "live"), d)

    assert main(["stop"]) == 0
    assert "iterm-dash is not running" in capsys.readouterr().out
    assert read_server_info(d) is None


def test_stop_asks_server_to_stop_and_waits_for_exit(private_state_dir, api_server, doomed, capsys):
    d = private_state_dir
    d.mkdir(parents=True)
    write_server_info(ServerInfo(doomed.pid, api_server.port, TOKEN, "live"), d)
    api_server.on_stop = lambda: (doomed.kill(), doomed.wait())

    assert main(["stop"]) == 0
    out = capsys.readouterr().out
    assert api_server.stopped == 1
    assert doomed.poll() is not None
    assert "stopped" in out
    assert TOKEN not in out


# -- iterm-dash (open) --------------------------------------------------------------


def test_open_without_browser_plugin_prints_install_steps(private_state_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "PLUGIN_PATHS", (tmp_path / "missing" / "iTermBrowserPlugin.app",))

    assert main([]) == 1
    err = capsys.readouterr().err
    assert "https://iterm2.com/downloads/browser-plugin/iTermBrowserPlugin-1.0.zip" in err
    assert "/Applications" in err
    assert "iterm-dash again" in err
    assert read_server_info(private_state_dir) is None


def test_browser_plugin_installed_checks_each_path(tmp_path):
    present = tmp_path / "Apps" / "iTermBrowserPlugin.app"
    absent = tmp_path / "Nope" / "iTermBrowserPlugin.app"
    assert browser_plugin_installed((absent,)) is False
    present.mkdir(parents=True)
    assert browser_plugin_installed((absent, present)) is True


def test_open_asks_running_server_to_open_the_dashboard(private_state_dir, tmp_path, api_server, monkeypatch, capsys):
    plugin = tmp_path / "iTermBrowserPlugin.app"
    plugin.mkdir()
    monkeypatch.setattr(cli, "PLUGIN_PATHS", (plugin,))
    write_server_info(ServerInfo(os.getpid(), api_server.port, TOKEN, "live"), private_state_dir)

    assert main([]) == 0
    captured = capsys.readouterr()
    assert api_server.opened == 1
    assert TOKEN not in captured.out + captured.err


def test_open_reports_a_server_that_does_not_start(private_state_dir, tmp_path, monkeypatch, capsys):
    plugin = tmp_path / "iTermBrowserPlugin.app"
    plugin.mkdir()
    monkeypatch.setattr(cli, "PLUGIN_PATHS", (plugin,))

    def failing(mode, d, **_kw):
        raise ServerStartError("the server did not start")

    monkeypatch.setattr(cli, "ensure_server", failing)
    assert main([]) == 1
    assert "server.log" in capsys.readouterr().err


# -- iterm-dash demo --------------------------------------------------------------


def test_demo_uses_separate_state_dir_and_prints_url(private_state_dir, monkeypatch, capsys):
    seen = []

    def fake_ensure(mode, d, **_kw):
        seen.append((mode, d))
        return ServerInfo(4242, 54321, "demo-token", "demo")

    monkeypatch.setattr(cli, "ensure_server", fake_ensure)
    assert main(["demo"]) == 0
    assert seen == [("demo", private_state_dir / "demo")]
    assert (private_state_dir / "demo").is_dir()
    assert "http://127.0.0.1:54321/?token=demo-token" in capsys.readouterr().out


# -- surface.open_dashboard ----------------------------------------------------------


class FakeSession:
    def __init__(self, sid, job=None, events=None):
        self.session_id = sid
        self.job = job  # None for a browser session; "raise" makes the variable read fail
        self.activations = []
        self.closes = []
        self.activate_error = None
        self.events = events if events is not None else []

    async def async_activate(self, select_tab=True, order_window_front=True):
        self.events.append(("activate", self.session_id))
        if self.activate_error is not None:
            raise self.activate_error
        self.activations.append((select_tab, order_window_front))

    async def async_get_variable(self, name):
        assert name == "jobName"
        if self.job == "raise":
            raise iterm2.RPCException("gone")
        return self.job

    async def async_close(self, force=False):
        self.events.append(("close", self.session_id))
        self.closes.append(force)


class FakeTab:
    def __init__(self, session):
        self.current_session = session
        self.sessions = [session]


class FakeWindow:
    def __init__(self, new_sid="NEW-TAB"):
        self.created = []
        self.new_session = FakeSession(new_sid)

    async def async_create_tab(self, profile_customizations=None, **kw):
        self.created.append(profile_customizations.values)
        return FakeTab(self.new_session)


class FakeApp:
    def __init__(self, sessions=(), window=None):
        self.sessions = {s.session_id: s for s in sessions}
        self.current_terminal_window = window
        self.app_activations = []

    def get_session_by_id(self, sid, include_buried=True):
        return self.sessions.get(sid)

    async def async_activate(self, raise_all_windows=True, ignoring_other_apps=False):
        self.app_activations.append((raise_all_windows, ignoring_other_apps))


def use_app(monkeypatch, app):
    async def fake_get_app(connection, create_if_needed=True):
        return app

    monkeypatch.setattr(iterm2, "async_get_app", fake_get_app)


URL = "http://127.0.0.1:5555/?token=abc"


def test_open_dashboard_brings_existing_tab_forward(monkeypatch):
    from iterm_dash.surface import open_dashboard

    existing = FakeSession("DASH-1")
    stale = FakeSession("OLD")
    window = FakeWindow()
    app = FakeApp([existing, stale], window)
    use_app(monkeypatch, app)

    assert asyncio.run(open_dashboard(object(), URL, "DASH-1", stale_session_id="OLD")) == "DASH-1"
    assert window.created == []
    assert stale.closes == []  # our own live tab wins; the stale one is untouched
    assert existing.activations == [(True, True)]
    assert app.app_activations == [(False, True)]


def test_open_dashboard_opens_browser_tab_when_previous_one_is_gone(monkeypatch):
    from iterm_dash.surface import open_dashboard

    window = FakeWindow("NEW-TAB")
    app = FakeApp([], window)
    use_app(monkeypatch, app)

    assert asyncio.run(open_dashboard(object(), URL, "DASH-GONE")) == "NEW-TAB"
    assert window.created == [{"Custom Command": json.dumps("Browser"), "Initial URL": json.dumps(URL)}]
    assert window.new_session.activations == [(True, True)]


def test_open_dashboard_closes_stale_browser_tab_and_opens_new(monkeypatch):
    from iterm_dash.surface import open_dashboard

    stale = FakeSession("OLD", job=None)
    window = FakeWindow("NEW-TAB")
    app = FakeApp([stale], window)
    use_app(monkeypatch, app)

    assert asyncio.run(open_dashboard(object(), URL, None, stale_session_id="OLD")) == "NEW-TAB"
    assert stale.closes == [True]
    assert stale.activations == []
    assert len(window.created) == 1
    assert window.new_session.activations == [(True, True)]


@pytest.mark.parametrize("job", ["zsh", "2.1.310", "raise"])
def test_open_dashboard_never_closes_a_non_browser_session(monkeypatch, job):
    from iterm_dash.surface import open_dashboard

    other = FakeSession("OLD", job=job)
    window = FakeWindow("NEW-TAB")
    app = FakeApp([other], window)
    use_app(monkeypatch, app)

    assert asyncio.run(open_dashboard(object(), URL, None, stale_session_id="OLD")) == "NEW-TAB"
    assert other.closes == []
    assert other.activations == []
    assert len(window.created) == 1


def test_open_dashboard_reports_a_new_tab_before_anything_else_can_fail(monkeypatch):
    from iterm_dash.surface import open_dashboard

    events = []
    stale = FakeSession("OLD", job=None, events=events)
    window = FakeWindow("NEW-TAB")
    window.new_session.events = events
    window.new_session.activate_error = iterm2.RPCException("bring-forward failed")
    use_app(monkeypatch, FakeApp([stale], window))

    with pytest.raises(iterm2.RPCException):
        asyncio.run(open_dashboard(object(), URL, None, stale_session_id="OLD", on_created=lambda sid: events.append(("created", sid))))
    assert events == [("created", "NEW-TAB"), ("close", "OLD"), ("activate", "NEW-TAB")]


def test_open_dashboard_does_not_report_a_tab_it_only_brought_forward(monkeypatch):
    from iterm_dash.surface import open_dashboard

    created = []
    use_app(monkeypatch, FakeApp([FakeSession("DASH-1")], FakeWindow()))
    assert asyncio.run(open_dashboard(object(), URL, "DASH-1", on_created=created.append)) == "DASH-1"
    assert created == []


def test_open_dashboard_creates_window_when_none_is_open(monkeypatch):
    from iterm_dash.surface import open_dashboard

    app = FakeApp([], None)
    use_app(monkeypatch, app)
    created = []
    new_session = FakeSession("WIN-TAB")

    async def fake_create(connection, profile=None, command=None, profile_customizations=None):
        created.append(profile_customizations.values)
        return SimpleNamespace(current_tab=FakeTab(new_session), tabs=[FakeTab(new_session)])

    monkeypatch.setattr(iterm2.Window, "async_create", fake_create)

    assert asyncio.run(open_dashboard(object(), URL, None)) == "WIN-TAB"
    assert created == [{"Custom Command": json.dumps("Browser"), "Initial URL": json.dumps(URL)}]


# -- server wiring (live on_open) -----------------------------------------------------


class FakeConnection:
    def __init__(self):
        self.closed = asyncio.Event()
        self.websocket = self

    async def wait_closed(self):
        await self.closed.wait()


class FakeBackend:
    instances = []

    def __init__(self, connection, hub, *, app=None):
        self.app = app
        self.excluded = []
        self.excluded_at_run = None
        FakeBackend.instances.append(self)

    def exclude(self, sid):
        self.excluded.append(sid)

    async def focus(self, sid):
        pass

    async def run(self):
        self.excluded_at_run = list(self.excluded)  # what the first discovery would see
        await asyncio.Event().wait()


def run_live_server(tmp_path, monkeypatch, open_impl, body):
    import iterm_dash.backend
    import iterm_dash.surface
    from iterm_dash.server import serve

    FakeBackend.instances = []
    shared_app = object()

    async def fake_get_app(connection, create_if_needed=True):
        return shared_app

    monkeypatch.setattr(iterm2, "async_get_app", fake_get_app)
    monkeypatch.setattr(iterm_dash.backend, "ItermBackend", FakeBackend)
    monkeypatch.setattr(iterm_dash.surface, "open_dashboard", open_impl)

    async def go():
        task = asyncio.create_task(serve("live", tmp_path, connection=FakeConnection()))
        for _ in range(200):
            info = read_server_info(tmp_path)
            if info is not None:
                break
            await asyncio.sleep(0.02)
        base = f"http://127.0.0.1:{info.port}"
        q = {"token": info.token}
        async with aiohttp.ClientSession() as http:
            result = await body(http, base, q, info)
            async with http.post(f"{base}/api/stop", params=q) as r:
                assert r.status == 200
        await asyncio.wait_for(task, 3)
        return result

    return asyncio.run(go())


def test_live_open_opens_dashboard_once_then_brings_it_forward(tmp_path, monkeypatch):
    calls = []

    async def fake_open(connection, url, existing_session_id, *, stale_session_id=None, on_created=None):
        calls.append((url, existing_session_id))
        return "DASH-1"

    async def body(http, base, q, info):
        for _ in range(2):
            async with http.post(f"{base}/api/open", params=q) as r:
                assert r.status == 200
        return info

    info = run_live_server(tmp_path, monkeypatch, fake_open, body)
    url = f"http://127.0.0.1:{info.port}/?token={info.token}"
    assert calls == [(url, None), (url, "DASH-1")]
    assert FakeBackend.instances[0].app is not None  # serve built the shared App up front
    assert FakeBackend.instances[0].excluded[0] == "DASH-1"
    assert set(FakeBackend.instances[0].excluded) == {"DASH-1"}


def test_live_open_failure_answers_500_without_leaking_token(tmp_path, monkeypatch, caplog):
    async def failing_open(connection, url, existing_session_id, *, stale_session_id=None, on_created=None):
        raise iterm2.CreateTabException("boom")

    async def body(http, base, q, info):
        async with http.post(f"{base}/api/open", params=q) as r:
            return r.status, info

    with caplog.at_level("INFO"):
        status, info = run_live_server(tmp_path, monkeypatch, failing_open, body)
    assert status == 500
    assert FakeBackend.instances[0].excluded == []
    assert info.token not in caplog.text


def test_live_open_excludes_and_persists_a_new_tab_even_if_bring_forward_fails(tmp_path, monkeypatch):
    calls = []

    async def open_then_fail(connection, url, existing_session_id, *, stale_session_id=None, on_created=None):
        calls.append(existing_session_id)
        if existing_session_id is None:
            on_created("NEW")
            raise iterm2.RPCException("bring-forward failed")
        return existing_session_id

    async def body(http, base, q, info):
        async with http.post(f"{base}/api/open", params=q) as r:
            assert r.status == 500
        assert "NEW" in FakeBackend.instances[0].excluded  # before /api/open even answered
        assert json.loads((tmp_path / "dashboard.json").read_text()) == {"session_id": "NEW"}
        async with http.post(f"{base}/api/open", params=q) as r:
            assert r.status == 200

    run_live_server(tmp_path, monkeypatch, open_then_fail, body)
    assert calls == [None, "NEW"]  # the next open brings the created tab forward, no second tab


# -- R14: the dashboard id survives server restarts ----------------------------------


async def _wait_for_backend_run():
    for _ in range(200):
        if FakeBackend.instances and FakeBackend.instances[0].excluded_at_run is not None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("backend never ran")


def test_live_startup_excludes_persisted_dashboard(tmp_path, monkeypatch):
    (tmp_path / "dashboard.json").write_text(json.dumps({"session_id": "OLD"}))

    async def no_open(connection, url, existing_session_id, *, stale_session_id=None, on_created=None):
        raise AssertionError("not opened in this test")

    async def body(http, base, q, info):
        await _wait_for_backend_run()

    run_live_server(tmp_path, monkeypatch, no_open, body)
    assert FakeBackend.instances[0].excluded_at_run == ["OLD"]


def test_live_open_replaces_stale_dashboard_and_persists_new_id(tmp_path, monkeypatch):
    (tmp_path / "dashboard.json").write_text(json.dumps({"session_id": "OLD"}))
    calls = []

    async def fake_open(connection, url, existing_session_id, *, stale_session_id=None, on_created=None):
        calls.append((existing_session_id, stale_session_id))
        return "NEW"

    async def body(http, base, q, info):
        for _ in range(2):
            async with http.post(f"{base}/api/open", params=q) as r:
                assert r.status == 200

    run_live_server(tmp_path, monkeypatch, fake_open, body)
    assert calls == [(None, "OLD"), ("NEW", None)]
    # Persisted, private, and left in place by /api/stop: the next server replaces the tab.
    path = tmp_path / "dashboard.json"
    assert json.loads(path.read_text()) == {"session_id": "NEW"}
    assert path.stat().st_mode & 0o777 == 0o600
    backend = FakeBackend.instances[0]
    assert backend.excluded[0] == "OLD"
    assert "NEW" in backend.excluded


@pytest.mark.parametrize(
    "content",
    [None, "{not json", "[]", '{"session_id": 5}', '{"session_id": ""}', '{"other": "x"}'],
)
def test_missing_or_corrupt_dashboard_file_is_ignored(tmp_path, monkeypatch, content):
    from iterm_dash.server import read_dashboard_id

    if content is not None:
        (tmp_path / "dashboard.json").write_text(content)
    assert read_dashboard_id(tmp_path) is None
    stale_ids = []

    async def fake_open(connection, url, existing_session_id, *, stale_session_id=None, on_created=None):
        stale_ids.append(stale_session_id)
        return "NEW"

    async def body(http, base, q, info):
        await _wait_for_backend_run()
        async with http.post(f"{base}/api/open", params=q) as r:
            assert r.status == 200

    run_live_server(tmp_path, monkeypatch, fake_open, body)
    assert FakeBackend.instances[0].excluded_at_run == []
    assert stale_ids == [None]
    assert read_dashboard_id(tmp_path) == "NEW"


def test_demo_open_persists_no_dashboard_id(tmp_path):
    from iterm_dash.server import serve

    async def go():
        task = asyncio.create_task(serve("demo", tmp_path))
        for _ in range(200):
            info = read_server_info(tmp_path)
            if info is not None:
                break
            await asyncio.sleep(0.02)
        base = f"http://127.0.0.1:{info.port}"
        q = {"token": info.token}
        async with aiohttp.ClientSession() as http:
            async with http.post(f"{base}/api/open", params=q) as r:
                assert r.status == 200
            async with http.post(f"{base}/api/stop", params=q) as r:
                assert r.status == 200
        await asyncio.wait_for(task, 3)

    asyncio.run(go())
    assert not (tmp_path / "dashboard.json").exists()
