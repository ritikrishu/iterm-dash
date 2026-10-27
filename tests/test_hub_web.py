import asyncio
import json
from contextlib import asynccontextmanager

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

import iterm_dash.hub as hub_mod
from iterm_dash.hub import Hub, ScreenFrame
from iterm_dash.registry import SessionInfo
from iterm_dash.state import Settings
from iterm_dash.status import StatusView
from iterm_dash.web import make_app

TOKEN = "tok-123"


def sess(i):
    return SessionInfo(id=i, title=f"t{i}", job="zsh", is_claude=False, cols=10, rows=3)


def frame(cols=10, rows=3, changed=None):
    lines = [[[f"row{r}", None, None, 0]] for r in range(rows)]
    if changed is not None:
        lines[changed] = [["CHANGED", None, None, 0]]
    return ScreenFrame(cols=cols, rows=rows, fg="#ffffff", bg="#000000", lines=lines, ts=1.0)


class Env:
    def __init__(self):
        self.saved = []
        self.focused = []
        self.opened = 0
        self.stopped = 0

        async def on_focus(i):
            self.focused.append(i)

        async def on_open():
            self.opened += 1

        async def on_stop():
            self.stopped += 1

        self.hub = Hub(Settings(), self.saved.append, on_focus)
        self.app = make_app(self.hub, TOKEN, on_open=on_open, on_stop=on_stop)


@asynccontextmanager
async def env():
    e = Env()
    server = TestServer(e.app)
    client = TestClient(server)
    await client.start_server()
    e.client = client
    try:
        yield e
    finally:
        await client.close()


def run(coro):
    asyncio.run(coro)


async def recv(ws):
    return json.loads((await asyncio.wait_for(ws.receive(), 2)).data)


async def drain_initial(ws, n):
    return [await recv(ws) for _ in range(n)]


def test_index_requires_token():
    async def main():
        async with env() as e:
            r = await e.client.get("/")
            assert r.status == 403
            r = await e.client.get("/", params={"token": "wrong"})
            assert r.status == 403
            r = await e.client.get("/", params={"token": TOKEN})
            assert r.status == 200
            assert 'id="grid"' in await r.text()

    run(main())


def test_ws_rejects_bad_token_and_foreign_origin():
    async def main():
        async with env() as e:
            r = await e.client.get("/ws", params={"token": "bad"})
            assert r.status == 403
            r = await e.client.get(
                "/ws", params={"token": TOKEN}, headers={"Origin": "http://evil.example"}
            )
            assert r.status == 403

    run(main())


def test_ws_accepts_its_own_origin_and_rejects_localhost():
    async def main():
        async with env() as e:
            port = e.client.port
            ws = await e.client.ws_connect(
                "/ws", params={"token": TOKEN}, headers={"Origin": f"http://127.0.0.1:{port}"}
            )
            assert (await recv(ws))["type"] == "hello"
            await ws.close()
            with pytest.raises(aiohttp.WSServerHandshakeError) as err:
                await e.client.ws_connect(
                    "/ws", params={"token": TOKEN}, headers={"Origin": f"http://localhost:{port}"}
                )
            assert err.value.status == 403

    run(main())


def test_new_client_gets_full_state():
    async def main():
        async with env() as e:
            await e.hub.set_sessions([sess("A"), sess("B")])
            await e.hub.set_screen("A", frame())
            await e.hub.set_status("B", StatusView("waiting", "Waiting", "#ff9500", None), 5.0)
            ws = await e.client.ws_connect("/ws", params={"token": TOKEN})
            msgs = await drain_initial(ws, 5)
            assert [m["type"] for m in msgs] == ["hello", "settings", "sessions", "screen", "status"]
            assert msgs[0]["protocol"] == 1
            assert msgs[3]["full"] is True
            assert set(msgs[3]["lines"]) == {"0", "1", "2"}
            assert msgs[4]["needs_you"] is True and msgs[4]["changed_at"] == 5.0
            assert msgs[2]["sessions"][0]["hidden"] is False
            await ws.close()

    run(main())


def test_screen_diff_sends_only_changed_rows():
    async def main():
        async with env() as e:
            await e.hub.set_sessions([sess("A")])
            ws = await e.client.ws_connect("/ws", params={"token": TOKEN})
            await drain_initial(ws, 3)
            await e.hub.set_screen("A", frame())
            assert (await recv(ws))["full"] is True
            await e.hub.set_screen("A", frame())  # identical: nothing
            await e.hub.set_screen("A", frame(changed=1))
            m = await recv(ws)
            assert m["type"] == "screen" and m["full"] is False
            assert list(m["lines"]) == ["1"]
            await ws.close()

    run(main())


def test_unchanged_session_list_is_not_sent_again():
    async def main():
        async with env() as e:
            await e.hub.set_sessions([sess("A")])
            ws = await e.client.ws_connect("/ws", params={"token": TOKEN})
            await drain_initial(ws, 3)
            await e.hub.set_sessions([sess("A")])  # e.g. a periodic pass that found nothing new
            await e.hub.set_sessions([sess("A"), sess("B")])
            m = await recv(ws)
            assert [s["id"] for s in m["sessions"]] == ["A", "B"]
            await ws.close()

    run(main())


def test_screen_resize_sends_full_frame():
    async def main():
        async with env() as e:
            await e.hub.set_sessions([sess("A")])
            ws = await e.client.ws_connect("/ws", params={"token": TOKEN})
            await drain_initial(ws, 3)
            await e.hub.set_screen("A", frame())
            await recv(ws)
            await e.hub.set_screen("A", frame(cols=20))
            m = await recv(ws)
            assert m["full"] is True and m["cols"] == 20
            assert set(m["lines"]) == {"0", "1", "2"}
            await ws.close()

    run(main())


def test_focus_hide_unhide_and_settings_messages():
    async def main():
        async with env() as e:
            await e.hub.set_sessions([sess("A")])
            ws = await e.client.ws_connect("/ws", params={"token": TOKEN})
            await drain_initial(ws, 3)
            await ws.send_json({"type": "focus", "id": "A"})
            await ws.send_json({"type": "hide", "id": "A"})
            m = await recv(ws)
            assert m["type"] == "sessions" and m["sessions"][0]["hidden"] is True
            assert e.focused == ["A"]
            await ws.send_json({"type": "unhide", "id": "A"})
            m = await recv(ws)
            assert m["sessions"][0]["hidden"] is False
            await ws.send_json({"type": "settings", "min_font_px": 100})
            m = await recv(ws)
            assert m["type"] == "settings" and m["min_font_px"] == 16
            assert e.saved[-1].min_font_px == 16
            await ws.send_json({"type": "settings", "show_all": "yes", "min_font_px": 1})
            m = await recv(ws)
            assert m["show_all"] is False and m["min_font_px"] == 4
            await ws.close()

    run(main())


def test_vanished_session_drops_cached_state():
    async def main():
        async with env() as e:
            await e.hub.set_sessions([sess("A"), sess("B")])
            await e.hub.set_screen("A", frame())
            await e.hub.set_status("A", StatusView("done", "Done", None, None), 1.0)
            ws = await e.client.ws_connect("/ws", params={"token": TOKEN})
            await drain_initial(ws, 5)
            await ws.send_json({"type": "hide", "id": "A"})
            assert (await recv(ws))["sessions"][0]["hidden"] is True
            await e.hub.set_sessions([sess("B")])
            assert [s["id"] for s in (await recv(ws))["sessions"]] == ["B"]
            await e.hub.set_sessions([sess("A"), sess("B")])
            m = await recv(ws)
            assert m["sessions"][0]["id"] == "A" and m["sessions"][0]["hidden"] is False
            await ws.close()

            ws2 = await e.client.ws_connect("/ws", params={"token": TOKEN})
            types = [m["type"] for m in await drain_initial(ws2, 3)]
            assert types == ["hello", "settings", "sessions"]
            await ws2.send_json({"type": "settings", "show_all": True})
            assert (await recv(ws2))["type"] == "settings"  # nothing cached was queued before it
            await ws2.close()

    run(main())


def test_screen_color_change_sends_full_frame():
    async def main():
        async with env() as e:
            await e.hub.set_sessions([sess("A")])
            ws = await e.client.ws_connect("/ws", params={"token": TOKEN})
            await drain_initial(ws, 3)
            await e.hub.set_screen("A", frame())
            await recv(ws)
            f = frame()
            await e.hub.set_screen("A", ScreenFrame(f.cols, f.rows, f.fg, "#101010", f.lines, f.ts))
            m = await recv(ws)
            assert m["full"] is True and m["bg"] == "#101010"
            assert set(m["lines"]) == {"0", "1", "2"}
            await ws.close()

    run(main())


def test_api_open_stop_health():
    async def main():
        async with env() as e:
            r = await e.client.get("/api/health")
            assert r.status == 403
            r = await e.client.get("/api/health", params={"token": TOKEN})
            body = await r.json()
            assert body["ok"] is True and body["clients"] == 0 and isinstance(body["pid"], int)
            assert (await e.client.post("/api/open")).status == 403
            assert (await e.client.post("/api/open", params={"token": TOKEN})).status == 200
            assert (await e.client.post("/api/stop", params={"token": TOKEN})).status == 200
            assert (e.opened, e.stopped) == (1, 1)

    run(main())


# -- stalled clients ------------------------------------------------------------------


class FakeWS:
    """A page double: records what it is sent; ``stall`` makes every later send hang forever."""

    def __init__(self, stall=False):
        self.sent = []
        self.stall = stall
        self.close_codes = []
        self._closed = asyncio.Event()

    async def send_str(self, text):
        if self.stall:
            await asyncio.Event().wait()
        self.sent.append(json.loads(text))

    async def close(self, *, code=aiohttp.WSCloseCode.OK, message=b"", drain=True):
        self.close_codes.append(code)
        self._closed.set()
        return True

    def __aiter__(self):
        return self._messages()

    async def _messages(self):
        await self._closed.wait()  # no messages from the page; ends once closed
        return
        yield


async def until(pred, timeout=2.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.005)


async def _no_focus(_sid):
    pass


@pytest.fixture
def short_send_timeout(monkeypatch):
    assert hub_mod._SEND_TIMEOUT == 2.0  # the real rule: a client gets 2 s per send
    monkeypatch.setattr(hub_mod, "_SEND_TIMEOUT", 0.2)  # the same rule, quicker to test
    return 0.2


def test_stalled_client_is_dropped_and_others_still_get_broadcasts(short_send_timeout):
    async def main():
        hub = Hub(Settings(), lambda _s: None, _no_focus)
        good, stuck = FakeWS(), FakeWS()
        tasks = [asyncio.create_task(hub.serve_client(ws)) for ws in (good, stuck)]
        await until(lambda: len(good.sent) == len(stuck.sent) == 3)
        stuck.stall = True
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await hub.set_sessions([sess("A")])
        assert loop.time() - t0 < short_send_timeout + 0.3  # bounded by the timeout, not by the stuck page
        assert good.sent[-1]["type"] == "sessions"
        assert hub.client_count == 1
        await asyncio.wait_for(tasks[1], 1)  # its socket was closed, so its handler ended
        assert stuck.close_codes == [aiohttp.WSCloseCode.GOING_AWAY]
        t0 = loop.time()
        await hub.set_status("A", StatusView("working", "Working", None, None), 1.0)
        assert loop.time() - t0 < 0.1  # nothing waits on the dropped page any more
        assert good.sent[-1]["type"] == "status"
        await good.close()
        await asyncio.wait_for(tasks[0], 1)

    run(main())


def test_client_stalled_during_initial_state_is_dropped(short_send_timeout):
    async def main():
        hub = Hub(Settings(), lambda _s: None, _no_focus)
        good, stuck = FakeWS(), FakeWS(stall=True)
        good_task = asyncio.create_task(hub.serve_client(good))
        await until(lambda: len(good.sent) == 3)
        stuck_task = asyncio.create_task(hub.serve_client(stuck))
        await asyncio.sleep(0)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await hub.set_sessions([sess("A")])  # waits on the stuck page's initial state, but not forever
        assert loop.time() - t0 < short_send_timeout + 0.3
        assert good.sent[-1]["type"] == "sessions"
        await asyncio.wait_for(stuck_task, 1)  # its handler ends without raising
        assert stuck.close_codes == [aiohttp.WSCloseCode.GOING_AWAY]
        assert hub.client_count == 1
        await good.close()
        await asyncio.wait_for(good_task, 1)

    run(main())
