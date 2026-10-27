"""Server-side state cache and broadcaster for connected pages."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

import aiohttp
from aiohttp import web

from iterm_dash.registry import SessionInfo
from iterm_dash.render import Row, diff_rows
from iterm_dash.state import Settings
from iterm_dash.status import StatusView

_log = logging.getLogger(__name__)
PROTOCOL = 1
MIN_FONT, MAX_FONT = 4.0, 16.0
_SEND_TIMEOUT = 2.0  # seconds a page gets to take one message; a stalled page is dropped


@dataclass(frozen=True)
class ScreenFrame:
    cols: int
    rows: int
    fg: str
    bg: str
    lines: list[Row]
    ts: float


class Hub:
    def __init__(
        self,
        settings: Settings,
        save_settings: Callable[[Settings], None],
        on_focus: Callable[[str], Awaitable[None]],
    ) -> None:
        self._settings = settings
        self._save = save_settings
        self._on_focus = on_focus
        self._sessions: list[SessionInfo] = []
        self._hidden: set[str] = set()
        self._screens: dict[str, ScreenFrame] = {}
        self._statuses: dict[str, tuple[StatusView, float]] = {}
        # Each client's lock keeps its initial state ahead of any broadcast.
        self._clients: dict[web.WebSocketResponse, asyncio.Lock] = {}
        self._closers: set[asyncio.Task] = set()  # best-effort closes of dropped clients

    @property
    def client_count(self) -> int:
        return len(self._clients)

    # -- message builders ---------------------------------------------------
    def _settings_msg(self) -> dict:
        return {
            "type": "settings",
            "show_all": self._settings.show_all,
            "min_font_px": self._settings.min_font_px,
        }

    def _sessions_msg(self) -> dict:
        return {
            "type": "sessions",
            "sessions": [
                {
                    "id": s.id,
                    "title": s.title,
                    "job": s.job or "",
                    "is_claude": s.is_claude,
                    "hidden": s.id in self._hidden,
                    "cols": s.cols,
                    "rows": s.rows,
                }
                for s in self._sessions
            ],
        }

    @staticmethod
    def _screen_msg(sid: str, f: ScreenFrame, lines: dict[int, Row], full: bool) -> dict:
        return {
            "type": "screen",
            "id": sid,
            "cols": f.cols,
            "rows": f.rows,
            "fg": f.fg,
            "bg": f.bg,
            "full": full,
            "ts": f.ts,
            "lines": {str(i): row for i, row in lines.items()},
        }

    @staticmethod
    def _status_msg(sid: str, v: StatusView, changed_at: float) -> dict:
        return {
            "type": "status",
            "id": sid,
            "tone": v.tone,
            "label": v.label,
            "color": v.color,
            "detail": v.detail,
            "needs_you": v.needs_you,
            "changed_at": changed_at,
        }

    # -- broadcasting -------------------------------------------------------
    async def _send(self, ws: web.WebSocketResponse, lock: asyncio.Lock, text: str) -> None:
        async def locked_send() -> None:
            async with lock:
                await ws.send_str(text)

        try:
            # The wait for the lock counts too: a page stuck on its initial state stalls nobody else.
            await asyncio.wait_for(locked_send(), _SEND_TIMEOUT)
        except Exception as exc:
            self._drop(ws, exc)

    def _drop(self, ws: web.WebSocketResponse, exc: BaseException) -> None:
        """Forget a client whose send failed or timed out, and close its socket in the background."""
        if self._clients.pop(ws, None) is None:
            return  # already dropped, or its handler is ending
        _log.info("client dropped after send %s; clients=%d", type(exc).__name__, len(self._clients))
        task = asyncio.create_task(self._close_quietly(ws))
        self._closers.add(task)
        task.add_done_callback(self._closers.discard)

    @staticmethod
    async def _close_quietly(ws: web.WebSocketResponse) -> None:
        try:  # best effort, and never waits long: a stalled peer may not answer the close
            await asyncio.wait_for(ws.close(code=aiohttp.WSCloseCode.GOING_AWAY, drain=False), _SEND_TIMEOUT)
        except Exception:
            pass

    async def _broadcast(self, msg: dict) -> None:
        text = json.dumps(msg)
        await asyncio.gather(*(self._send(ws, lk, text) for ws, lk in list(self._clients.items())))

    # -- backend-facing API -------------------------------------------------
    async def set_sessions(self, sessions: list[SessionInfo]) -> None:
        if list(sessions) == self._sessions:
            return  # nothing for the pages to redo (most periodic discovery passes)
        self._sessions = list(sessions)
        live = {s.id for s in sessions}
        self._hidden &= live
        for cache in (self._screens, self._statuses):
            for gone in [k for k in cache if k not in live]:
                del cache[gone]
        await self._broadcast(self._sessions_msg())

    async def set_screen(self, sid: str, frame: ScreenFrame) -> None:
        prev = self._screens.get(sid)
        full = prev is None or (prev.cols, prev.rows, prev.fg, prev.bg) != (
            frame.cols,
            frame.rows,
            frame.fg,
            frame.bg,
        )
        changed = diff_rows(None if full else prev.lines, frame.lines)
        self._screens[sid] = frame
        if not full and not changed:
            return
        await self._broadcast(self._screen_msg(sid, frame, changed, full))

    async def set_status(self, sid: str, view: StatusView, changed_at: float) -> None:
        self._statuses[sid] = (view, changed_at)
        await self._broadcast(self._status_msg(sid, view, changed_at))

    async def close_all(self) -> None:
        await asyncio.gather(*(self._close_quietly(ws) for ws in list(self._clients)), *list(self._closers))

    # -- client handling ----------------------------------------------------
    async def serve_client(self, ws: web.WebSocketResponse) -> None:
        initial = [{"type": "hello", "protocol": PROTOCOL}, self._settings_msg(), self._sessions_msg()]
        initial += [
            self._screen_msg(sid, f, dict(enumerate(f.lines)), True) for sid, f in self._screens.items()
        ]
        initial += [self._status_msg(sid, v, at) for sid, (v, at) in self._statuses.items()]
        lock = asyncio.Lock()
        await lock.acquire()  # unlocked, so this never suspends
        self._clients[ws] = lock
        _log.info("client connected; clients=%d", len(self._clients))
        try:
            try:
                for msg in initial:
                    await asyncio.wait_for(ws.send_str(json.dumps(msg)), _SEND_TIMEOUT)
            except TimeoutError as exc:
                self._drop(ws, exc)
                return
            finally:
                lock.release()
            async for raw in ws:
                if raw.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(raw.data)
                except ValueError:
                    continue
                if isinstance(data, dict):
                    await self._handle(data)
        except ConnectionError:
            pass
        finally:
            self._clients.pop(ws, None)
            _log.info("client disconnected; clients=%d", len(self._clients))

    async def _handle(self, data: dict) -> None:
        kind, sid = data.get("type"), data.get("id")
        known = isinstance(sid, str) and any(s.id == sid for s in self._sessions)
        if kind == "focus" and known:
            try:
                await self._on_focus(sid)
            except Exception as exc:
                _log.warning("focus failed: %s", type(exc).__name__)
        elif kind in ("hide", "unhide") and known:
            before = len(self._hidden)
            (self._hidden.add if kind == "hide" else self._hidden.discard)(sid)
            if len(self._hidden) != before:
                await self._broadcast(self._sessions_msg())
        elif kind == "settings":
            await self._update_settings(data)

    async def _update_settings(self, data: dict) -> None:
        s = self._settings
        show_all = data.get("show_all", s.show_all)
        if not isinstance(show_all, bool):
            show_all = s.show_all
        font = data.get("min_font_px", s.min_font_px)
        if isinstance(font, bool) or not isinstance(font, (int, float)) or font != font:
            font = s.min_font_px
        font = min(max(float(font), MIN_FONT), MAX_FONT)
        self._settings = replace(s, show_all=show_all, min_font_px=font)
        try:
            self._save(self._settings)
        except Exception as exc:
            _log.warning("saving settings failed: %s", type(exc).__name__)
        await self._broadcast(self._settings_msg())
