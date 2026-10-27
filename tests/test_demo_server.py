import asyncio
import json

import aiohttp

from iterm_dash.demo import DemoBackend
from iterm_dash.server import serve


class FakeHub:
    def __init__(self):
        self.sessions_calls = []
        self.statuses = {}
        self.frames = []

    async def set_sessions(self, sessions):
        self.sessions_calls.append(list(sessions))

    async def set_screen(self, sid, frame):
        self.frames.append(frame)

    async def set_status(self, sid, view, changed_at):
        self.statuses[sid] = view


def test_demo_backend_feeds_hub():
    async def main():
        hub = FakeHub()
        task = asyncio.create_task(DemoBackend(hub).run())
        await asyncio.sleep(0.6)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return hub

    hub = asyncio.run(main())
    assert len(hub.sessions_calls) == 1
    sessions = hub.sessions_calls[0]
    assert len(sessions) == 7
    assert sum(s.is_claude for s in sessions) == 6
    assert {v.tone for v in hub.statuses.values()} >= {"done", "working", "shell"}
    assert hub.frames
    assert all(f.cols == 214 and f.rows == 57 for f in hub.frames)
    assert all(len(f.lines) == 57 for f in hub.frames)
    assert all(sum(len(run[0]) for run in row) <= 214 for f in hub.frames for row in f.lines)


def test_demo_timeline_has_working_waiting_and_done_sessions():
    from iterm_dash.demo import _PROJECTS, CYCLE_SECONDS, status_at

    seen = {status_at(p, t / 2) for p in _PROJECTS for t in range(int(CYCLE_SECONDS * 2))}
    assert seen == {"working", "waiting", "idle"}
    assert status_at(_PROJECTS[2], 4) == "waiting"
    assert status_at(_PROJECTS[2], 4 + CYCLE_SECONDS) == "waiting"


def test_serve_demo_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("ITERM_DASH_STATE_DIR", str(tmp_path))

    async def main():
        task = asyncio.create_task(serve("demo", tmp_path))
        info_path = tmp_path / "server.json"
        for _ in range(100):
            if info_path.exists():
                break
            await asyncio.sleep(0.02)
        info = json.loads(info_path.read_text())
        base = f"http://127.0.0.1:{info['port']}"
        q = {"token": info["token"]}
        async with aiohttp.ClientSession() as http:
            async with http.get(f"{base}/api/health", params=q) as r:
                assert r.status == 200
                assert (await r.json())["ok"] is True
            async with http.post(f"{base}/api/stop", params=q) as r:
                assert r.status == 200
        await asyncio.wait_for(task, 2)
        return info_path.exists()

    assert asyncio.run(main()) is False


def test_stop_with_open_websocket_exits_fast(tmp_path, monkeypatch):
    monkeypatch.setenv("ITERM_DASH_STATE_DIR", str(tmp_path))

    async def main():
        task = asyncio.create_task(serve("demo", tmp_path))
        info_path = tmp_path / "server.json"
        for _ in range(100):
            if info_path.exists():
                break
            await asyncio.sleep(0.02)
        info = json.loads(info_path.read_text())
        base = f"http://127.0.0.1:{info['port']}"
        q = {"token": info["token"]}
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(f"{base}/ws", params=q) as ws:
                assert json.loads((await ws.receive(timeout=2)).data)["type"] == "hello"
                async with http.post(f"{base}/api/stop", params=q) as r:
                    assert r.status == 200
                await asyncio.wait_for(task, 3)
        return info_path.exists()

    assert asyncio.run(main()) is False


def test_connection_closed_helper_waits_for_close():
    from iterm_dash.server import _connection_closed

    class FakeWs:
        def __init__(self):
            self.ev = asyncio.Event()

        async def wait_closed(self):
            await self.ev.wait()

    class FakeConn:
        websocket = None

    async def main():
        conn = FakeConn()
        conn.websocket = FakeWs()
        t = asyncio.create_task(_connection_closed(conn))
        await asyncio.sleep(0.05)
        assert not t.done()
        conn.websocket.ev.set()
        await asyncio.wait_for(t, 1)

    asyncio.run(main())
