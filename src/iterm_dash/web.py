"""aiohttp app: the page, the WebSocket and a small token-guarded API."""

from __future__ import annotations

import hmac
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from aiohttp import web

from iterm_dash.hub import Hub

WEB_DIR = Path(__file__).parent / "web"


def make_app(
    hub: Hub,
    token: str,
    *,
    on_open: Callable[[], Awaitable[None]],
    on_stop: Callable[[], Awaitable[None]],
    static_dir: Path = WEB_DIR,
) -> web.Application:
    def check_token(request: web.Request) -> None:
        given = request.query.get("token", "")
        if not hmac.compare_digest(given.encode(), token.encode()):
            raise web.HTTPForbidden()

    async def index(request: web.Request) -> web.StreamResponse:
        check_token(request)
        return web.FileResponse(static_dir / "index.html")

    async def ws_handler(request: web.Request) -> web.StreamResponse:
        check_token(request)
        origin = request.headers.get("Origin")
        if origin is not None and origin != f"http://127.0.0.1:{request.url.port}":
            raise web.HTTPForbidden()
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        await hub.serve_client(ws)
        return ws

    async def health(request: web.Request) -> web.StreamResponse:
        check_token(request)
        return web.json_response({"ok": True, "pid": os.getpid(), "clients": hub.client_count})

    async def api_open(request: web.Request) -> web.StreamResponse:
        check_token(request)
        await on_open()
        return web.json_response({"ok": True})

    async def api_stop(request: web.Request) -> web.StreamResponse:
        check_token(request)
        await on_stop()
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/open", api_open)
    app.router.add_post("/api/stop", api_stop)
    app.router.add_static("/static/", static_dir)
    return app
