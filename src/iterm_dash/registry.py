"""Discover iTerm2 sessions and describe them."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass

_log = logging.getLogger(__name__)
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_TRAILING_GROUP_RE = re.compile(r"\s*\([^()]*\)\s*$")


@dataclass(frozen=True)
class SessionInfo:
    id: str
    title: str
    job: str | None
    is_claude: bool
    cols: int
    rows: int


def is_claude_session(job: str | None, command_line: str | None, has_status: bool) -> bool:
    if has_status:
        return True
    if job and _VERSION_RE.match(job):
        return True
    if command_line:
        parts = command_line.split()
        if parts and os.path.basename(parts[0]) == "claude":
            return True
    return False


def display_title(auto_name: str | None, name: str | None, job: str | None) -> str:
    if auto_name:
        return auto_name
    if name:
        stripped = _TRAILING_GROUP_RE.sub("", name).strip()
        if stripped:
            return stripped
    return job or "session"


async def list_sessions(
    app,
    exclude: Collection[str],
    has_status: Callable[[str], Awaitable[bool]],
) -> list[SessionInfo]:
    """Every session in iTerm2's order (windows, tabs, sessions). The backend keeps cell order fixed."""
    out: list[SessionInfo] = []
    for window in app.terminal_windows:
        for tab in window.tabs:
            for session in tab.sessions:
                sid = session.session_id
                if sid in exclude:
                    continue
                try:
                    auto_name = await session.async_get_variable("autoName")
                    name = await session.async_get_variable("name")
                    job = await session.async_get_variable("jobName")
                    command_line = await session.async_get_variable("commandLine")
                    size = session.grid_size
                    info = SessionInfo(
                        id=sid,
                        title=display_title(auto_name, name, job),
                        job=job,
                        is_claude=is_claude_session(job, command_line, await has_status(sid)),
                        cols=size.width,
                        rows=size.height,
                    )
                except Exception as e:
                    _log.warning("skipping session after %s", type(e).__name__)
                    continue
                out.append(info)
    return out
