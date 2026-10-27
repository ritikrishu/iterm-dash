"""Live iTerm2 backend: discovery, throttled previews, native status and focus.

Read-only toward the user's sessions. The only calls that change anything are
the two focus calls (``session.async_activate`` and ``app.async_activate``).
Logs carry ids, counts and timings only; exceptions are logged by type name,
because their messages can carry screen text.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import iterm2
from iterm2 import api_pb2
from iterm2 import rpc as iterm2_rpc
from iterm2.screen import LineContents

from iterm_dash.hub import ScreenFrame
from iterm_dash.palette import DEFAULT_PALETTE, Palette
from iterm_dash.pump import LatestOnlyPump
from iterm_dash.registry import SessionInfo, list_sessions
from iterm_dash.render import Row, line_row
from iterm_dash.status import StatusTracker, StatusView, classify

_log = logging.getLogger(__name__)
_RETRY_SECONDS = 1.0
_DEBOUNCE_SECONDS = 0.05
_DISCOVERY_INTERVAL = 10.0  # a periodic pass catches title changes and missed layout events
_BUFFER_OK = api_pb2.GetBufferResponse.Status.Value("OK")
# Passes that may follow a change iTerm2 didn't announce to the App, so they reload its hierarchy first.
# Discovery triggers: start, exclude, layout, new, terminated, status-flip, size-change, periodic.
_REFRESH_TRIGGERS = frozenset({"size-change", "periodic"})


@dataclass
class _LineCache:
    """Per-session render cache: the last frame's rows keyed by each line's raw proto bytes.

    Keyed by content, not position, so a scrolled screen reuses the rows that moved.
    Rows may be shared between lines and frames; they are never mutated downstream.
    """

    palette: Palette
    rows: dict[bytes, Row]


async def _get_buffer(session) -> api_pb2.GetBufferResponse:
    """The raw styled screen proto, fetched the way Session.async_get_screen_contents does."""
    result = await iterm2_rpc.async_get_screen_contents(session.connection, session.session_id, None, True)
    response = result.get_buffer_response
    if response.status != _BUFFER_OK:
        raise iterm2.RPCException(api_pb2.GetBufferResponse.Status.Name(response.status))
    return response


async def read_status(connection, session_id: str) -> dict | None:
    """iTerm2's native status dict for a session, or None when the read itself failed.

    A failed read (e.g. a timeout) carries no information. A session that isn't running
    Claude still reads successfully: its dict has ``status`` None, or it is ``{}`` when
    iTerm2 answered with something other than a dict.
    """
    try:
        result = await iterm2.async_invoke_function(
            connection, f'iterm2.get_session_status(session_id: "{session_id}")', timeout=2
        )
    except Exception as exc:
        _log.debug("status read failed for %s: %s", session_id, type(exc).__name__)
        return None
    return result if isinstance(result, dict) else {}


def has_claude_status(raw: dict | None) -> bool:
    return raw is not None and raw.get("status") is not None


async def _keep_running(name: str, body: Callable[[], Awaitable[None]]) -> None:
    """Run ``body`` and run it again shortly after it fails, until cancelled."""
    while True:
        try:
            await body()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                # A cleanup step (e.g. unsubscribing on a dead socket) replaced
                # our cancellation with its own error; stay cancelled.
                raise asyncio.CancelledError from None
            _log.warning("%s failed: %s", name, type(exc).__name__)
        await asyncio.sleep(_RETRY_SECONDS)


class ItermBackend:
    def __init__(
        self,
        connection,
        hub,
        *,
        preview_interval: float = 0.25,
        status_interval: float = 1.0,
        discovery_interval: float = _DISCOVERY_INTERVAL,
        app=None,
    ) -> None:
        self._conn = connection
        self._hub = hub
        self._preview_interval = preview_interval
        self._status_interval = status_interval
        self._discovery_interval = discovery_interval
        self._app = app
        self._excluded: set[str] = set()
        self._infos: dict[str, SessionInfo] = {}  # in cell order: first seen first
        self._watchers: dict[str, asyncio.Task] = {}
        self._pumps: dict[str, LatestOnlyPump] = {}
        self._palettes: dict[str, Palette] = {}
        self._palette_gen = 0
        self._line_cache: dict[str, _LineCache] = {}
        self._status_locks: dict[str, asyncio.Lock] = {}
        self._claude_seen: dict[str, bool] = {}
        self._last_view: dict[str, StatusView] = {}  # the view last pushed to the hub
        self._size_flagged: dict[str, tuple[int, int]] = {}  # (buffer rows, grid rows) already rediscovered
        self._tracker = StatusTracker()
        self._rediscover = asyncio.Event()
        self._triggers: set[str] = set()  # why the next discovery pass runs
        self._children: set[asyncio.Task] = set()

    # -- public API -----------------------------------------------------------
    def exclude(self, session_id: str) -> None:
        """Never watch this session (the dashboard's own tab)."""
        self._excluded.add(session_id)
        self._request_discovery("exclude")

    async def run(self) -> None:
        """Watch iTerm2 until the connection closes, then stop every child task."""
        closed = asyncio.create_task(self._conn.websocket.wait_closed())
        main = asyncio.create_task(self._main())
        try:
            done, _ = await asyncio.wait({main, closed}, return_when=asyncio.FIRST_COMPLETED)
            if closed in done:
                _log.info("iTerm2 connection closed; backend stopping")
            elif not main.cancelled() and main.exception() is not None:
                _log.error("backend failed: %s", type(main.exception()).__name__)
        finally:
            tasks = [main, closed, *self._children, *self._watchers.values()]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._children.clear()
            self._watchers.clear()
            self._pumps.clear()
            _log.info("backend stopped")

    async def focus(self, session_id: str) -> None:
        app = self._app
        session = app.get_session_by_id(session_id) if app is not None else None
        if session is None:
            _log.info("focus: session %s not found", session_id)
            return
        start = time.perf_counter()
        await session.async_activate(select_tab=True, order_window_front=True)
        await app.async_activate(raise_all_windows=False, ignoring_other_apps=True)
        _log.info("focus %s took %.0f ms", session_id, (time.perf_counter() - start) * 1000)

    async def fetch_frame(self, session) -> ScreenFrame | None:
        """A styled snapshot of the session, or None when it can't be read (e.g. gone)."""
        sid = session.session_id
        try:
            buffer = await _get_buffer(session)
        except Exception as exc:
            _log.debug("screen fetch failed for %s: %s", sid, type(exc).__name__)
            return None
        ts = time.time()
        size = session.grid_size
        if size is None:
            return None
        # grid_size is only refreshed by layout events; the buffer always has the screen's real rows.
        rows = len(buffer.contents)
        self._check_size(sid, rows, size.height)
        palette = await self._palette_for(session)
        lines = self._render(sid, buffer, palette)
        return ScreenFrame(size.width, rows, palette.fg, palette.bg, lines, ts)

    def _check_size(self, sid: str, rows: int, grid_rows: int) -> None:
        """Rediscover (with a refreshed App) once per new mismatch between the buffer and grid_size."""
        if rows == grid_rows:
            self._size_flagged.pop(sid, None)
            return
        if self._size_flagged.get(sid) != (rows, grid_rows):
            self._size_flagged[sid] = (rows, grid_rows)
            self._request_discovery("size-change")

    def _render(self, sid: str, buffer: api_pb2.GetBufferResponse, palette: Palette) -> list[Row]:
        """Rows for the buffer, rendering only lines whose content wasn't on the last frame."""
        cache = self._line_cache.get(sid)
        known = cache.rows if cache is not None and cache.palette == palette else {}
        current: dict[bytes, Row] = {}
        lines: list[Row] = []
        for line in buffer.contents:
            raw = line.SerializeToString()
            row = current.get(raw)
            if row is None:
                row = known.get(raw)
                if row is None:
                    row = line_row(LineContents(line), palette)
                current[raw] = row
            lines.append(row)
        # Only this frame's lines are kept, so the cache stays bounded to one screen.
        self._line_cache[sid] = _LineCache(palette, current)
        return lines

    # -- tasks ------------------------------------------------------------------
    async def _main(self) -> None:
        if self._app is None:
            self._app = await iterm2.async_get_app(self._conn)
        for name, body in (
            ("layout monitor", lambda: self._monitor(iterm2.LayoutChangeMonitor, "layout")),
            ("new-session monitor", lambda: self._monitor(iterm2.NewSessionMonitor, "new")),
            ("termination monitor", lambda: self._monitor(iterm2.SessionTerminationMonitor, "terminated")),
            ("theme monitor", self._theme_monitor),
            ("status loop", self._status_loop),
            ("periodic discovery", self._periodic_discovery),
        ):
            self._children.add(asyncio.create_task(_keep_running(name, body)))
        self._request_discovery("start")
        await _keep_running("discovery", self._discovery_loop)

    async def _monitor(self, factory, trigger: str) -> None:
        async with factory(self._conn) as mon:
            while True:
                await mon.async_get()
                if trigger == "layout":  # profile or size may have changed: refresh palettes and frames
                    self._recolour()
                self._request_discovery(trigger)

    async def _periodic_discovery(self) -> None:
        """A safety net next to the events: same debounced pass, so it never reorders cells."""
        while True:
            await asyncio.sleep(self._discovery_interval)
            self._request_discovery("periodic")

    async def _theme_monitor(self) -> None:
        """A light/dark switch is not a layout change; watch the app's effectiveTheme."""
        async with iterm2.VariableMonitor(self._conn, iterm2.VariableScopes.APP, "effectiveTheme", None) as mon:
            while True:
                await mon.async_get()
                _log.info("theme changed")
                self._line_cache.clear()
                self._recolour()

    def _request_discovery(self, trigger: str) -> None:
        self._triggers.add(trigger)
        self._rediscover.set()

    def _recolour(self) -> None:
        """Re-resolve every palette on the next fetch and fetch every session again."""
        self._palette_gen += 1
        self._palettes.clear()
        for pump in self._pumps.values():
            pump.mark_dirty()

    async def _discovery_loop(self) -> None:
        while True:
            await self._rediscover.wait()
            await asyncio.sleep(_DEBOUNCE_SECONDS)  # one pass for a burst of events
            self._rediscover.clear()
            triggers, self._triggers = self._triggers, set()
            try:
                await self._discover(triggers)
            except Exception:
                self._triggers |= triggers
                self._rediscover.set()  # retry the pass, not just wait for the next event
                raise

    async def _discover(self, triggers: set[str]) -> None:
        start = time.perf_counter()
        if triggers & _REFRESH_TRIGGERS:
            await self._app.async_refresh()  # a read: reloads windows, tabs and grid sizes
        found = {i.id: i for i in await list_sessions(self._app, self._excluded, self._has_status)}
        # Cells keep their positions: known sessions stay in first-seen order and new ones
        # (in iTerm2's order among themselves) go at the end. Moving tabs reorders nothing.
        order = [sid for sid in self._infos if sid in found] + [sid for sid in found if sid not in self._infos]
        self._infos = {sid: found[sid] for sid in order}
        infos = list(self._infos.values())
        gone = [sid for sid in self._watchers if sid not in self._infos]
        stopping = [self._watchers.pop(sid) for sid in gone]
        for task in stopping:
            task.cancel()
        for sid in gone:
            for cache in (
                self._pumps, self._palettes, self._line_cache, self._status_locks, self._claude_seen,
                self._last_view, self._size_flagged,
            ):
                cache.pop(sid, None)
            self._tracker.forget(sid)
        # Stop vanished sessions' tasks before the hub drops their cached state.
        await asyncio.gather(*stopping, return_exceptions=True)
        await self._hub.set_sessions(infos)
        added = 0
        for info in infos:
            if info.id in self._watchers:
                continue
            session = self._app.get_session_by_id(info.id)
            if session is not None:
                self._watchers[info.id] = asyncio.create_task(self._watch(session))
                added += 1
        _log.info(
            "discovery %s: sessions=%d added=%d removed=%d in %.0f ms",
            ",".join(sorted(triggers)), len(infos), added, len(gone), (time.perf_counter() - start) * 1000,
        )

    async def _watch(self, session) -> None:
        sid = session.session_id
        pump = LatestOnlyPump(lambda: self._publish(session), self._preview_interval)
        self._pumps[sid] = pump
        pump_task = asyncio.create_task(pump.run())
        pump.mark_dirty()

        async def stream() -> None:
            async with session.get_screen_streamer(want_contents=False) as streamer:
                pump.mark_dirty()  # anything that changed while subscribing
                while True:
                    await streamer.async_get()
                    pump.mark_dirty()

        try:
            await _keep_running(f"streamer {sid}", stream)
        finally:
            pump_task.cancel()
            await asyncio.gather(pump_task, return_exceptions=True)

    async def _publish(self, session) -> None:
        sid = session.session_id
        frame = await self.fetch_frame(session)
        if frame is not None and sid in self._infos:
            await self._hub.set_screen(sid, frame)
        await self._refresh_status(sid)

    async def _palette_for(self, session) -> Palette:
        sid = session.session_id
        cached = self._palettes.get(sid)
        if cached is not None:
            return cached
        gen = self._palette_gen
        try:
            profile = await session.async_get_profile()
            theme = await self._app.async_get_variable("effectiveTheme")
            palette = Palette.from_profile(profile, dark="dark" in (theme or ""))
        except Exception as exc:
            _log.info("default palette for %s: %s", sid, type(exc).__name__)
            return DEFAULT_PALETTE  # for this frame only: the next fetch tries the profile again
        if gen == self._palette_gen:  # not invalidated while we were fetching
            self._palettes[sid] = palette
        return palette

    # -- status -----------------------------------------------------------------
    async def _has_status(self, sid: str) -> bool:
        raw = await read_status(self._conn, sid)
        if raw is None:  # failed read: no new information, so keep what was last known
            return self._claude_seen.setdefault(sid, False)
        present = has_claude_status(raw)
        self._claude_seen[sid] = present
        return present

    async def _status_loop(self) -> None:
        while True:
            results = await asyncio.gather(
                *(self._refresh_status(sid) for sid in list(self._infos)), return_exceptions=True
            )
            for r in results:
                if isinstance(r, Exception):
                    _log.warning("status refresh failed: %s", type(r).__name__)
            await asyncio.sleep(self._status_interval)

    async def _refresh_status(self, sid: str) -> None:
        if sid not in self._infos:
            return
        async with self._status_locks.setdefault(sid, asyncio.Lock()):
            raw = await read_status(self._conn, sid)
            info = self._infos.get(sid)
            if info is None or raw is None:  # vanished while we were reading, or the read failed
                return
            present = has_claude_status(raw)
            if self._claude_seen.get(sid, present) != present:
                self._request_discovery("status-flip")  # Claude started or exited: refresh is_claude
            self._claude_seen[sid] = present
            view = classify(raw, is_claude=info.is_claude, job=info.job)
            # changed_at moves only with the tone or label; any other change (detail, colour) is still pushed.
            _, changed_at = self._tracker.update(sid, view)
            if view != self._last_view.get(sid):
                self._last_view[sid] = view
                await self._hub.set_status(sid, view, changed_at)
