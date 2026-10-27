"""Process entry point: hub, backend and web app on 127.0.0.1."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
from functools import partial
from pathlib import Path
from typing import Literal

from aiohttp import web

from iterm_dash.cli import dashboard_url, server_dir
from iterm_dash.hub import Hub
from iterm_dash.state import (
    ServerInfo,
    _write_private,
    clear_server_info,
    load_settings,
    save_settings,
    write_server_info,
)
from iterm_dash.web import make_app

_log = logging.getLogger(__name__)
_DASHBOARD_FILE = "dashboard.json"


def read_dashboard_id(d: Path) -> str | None:
    """The dashboard tab's session id from a previous server; None if missing or corrupt."""
    try:
        sid = json.loads((d / _DASHBOARD_FILE).read_text())["session_id"]
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None
    return sid if isinstance(sid, str) and sid else None


def write_dashboard_id(d: Path, session_id: str) -> None:
    _write_private(d / _DASHBOARD_FILE, json.dumps({"session_id": session_id}), 0o600)


async def _connection_closed(connection) -> None:
    await connection.websocket.wait_closed()


async def serve(mode: Literal["live", "demo"], d: Path, *, connection=None) -> None:
    backend = None

    async def on_focus(session_id: str) -> None:
        if backend is not None:
            await backend.focus(session_id)

    hub = Hub(load_settings(d), partial(save_settings, d=d), on_focus)
    previous_id: str | None = None  # a previous server's dashboard tab (live only)
    if mode == "demo":
        from iterm_dash.demo import DemoBackend

        backend = DemoBackend(hub)
    else:
        import iterm2

        from iterm_dash.backend import ItermBackend

        # One App for the backend and the dashboard opener: built here, before /api/open
        # can race the backend's first async_get_app into constructing a second one.
        app = await iterm2.async_get_app(connection)
        backend = ItermBackend(connection, hub, app=app)
        previous_id = read_dashboard_id(d)
        if previous_id is not None:
            backend.exclude(previous_id)  # before the first discovery: never shown in the grid

    stop = asyncio.Event()
    url = ""  # set once the port is known; /api/open can't arrive before that
    dashboard_id: str | None = None
    open_lock = asyncio.Lock()  # two quick `iterm-dash` runs must not open two tabs

    def adopt(new_id: str) -> None:
        """A new dashboard tab exists: exclude and persist it before any later step can fail."""
        nonlocal dashboard_id, previous_id
        backend.exclude(new_id)  # the dashboard never shows itself
        dashboard_id = new_id  # the next `iterm-dash` brings this tab forward
        previous_id = None  # replaced (or, if not ours, left alone but still excluded)
        try:
            write_dashboard_id(d, new_id)  # so the next server replaces it after a restart
        except OSError as exc:
            _log.warning("saving the dashboard id failed: %s", type(exc).__name__)

    async def on_open() -> None:
        if mode != "live":
            return
        from iterm_dash.surface import open_dashboard

        async with open_lock:
            try:
                new_id = await open_dashboard(
                    connection, url, dashboard_id, stale_session_id=previous_id, on_created=adopt
                )
            except Exception as exc:
                _log.error("opening the dashboard failed: %s", type(exc).__name__)
                raise web.HTTPInternalServerError() from None
            if new_id is None:
                raise web.HTTPInternalServerError()
            if new_id != dashboard_id:  # not reported through on_created; adopt it now
                adopt(new_id)

    async def on_stop() -> None:
        stop.set()  # scheduled; /api/stop must not wait on runner cleanup

    token = secrets.token_urlsafe(32)
    app = make_app(hub, token, on_open=on_open, on_stop=on_stop)
    app.on_shutdown.append(lambda _app: hub.close_all())
    runner = web.AppRunner(app, access_log=None, shutdown_timeout=2.0)
    await runner.setup()
    task: asyncio.Task | None = None
    wrote = False
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        info = ServerInfo(os.getpid(), port, token, mode)
        url = dashboard_url(info)
        write_server_info(info, d)
        wrote = True
        _log.info("serving mode=%s port=%d pid=%d", mode, port, os.getpid())
        task = asyncio.create_task(backend.run())
        stopper = asyncio.create_task(stop.wait())
        waiting = {task, stopper}
        guard = None
        if mode == "live":
            guard = asyncio.create_task(_connection_closed(connection))
            waiting.add(guard)
        done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
        stopper.cancel()
        if guard is not None:
            guard.cancel()
        if guard in done:
            _log.info("iTerm2 connection closed")
        if task in done and not task.cancelled() and task.exception():
            _log.error("backend failed: %s", type(task.exception()).__name__)
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if wrote:
            clear_server_info(d, os.getpid())
        await runner.cleanup()
        _log.info("server stopped")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="iterm_dash.server")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args(argv)
    d = server_dir("demo" if args.demo else "live")
    logging.basicConfig(
        filename=d / "server.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.demo:
        asyncio.run(serve("demo", d))
    else:
        import iterm2

        async def live(connection) -> None:
            await serve("live", d, connection=connection)

        iterm2.run_until_complete(live)


if __name__ == "__main__":
    main()
