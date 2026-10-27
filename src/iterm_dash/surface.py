"""Show the dashboard as an iTerm2 browser tab, or bring the open one forward.

The recipe (see docs/architecture.md, "The dashboard tab"):
a new tab whose profile customisation sets ``Custom Command`` to ``Browser`` and
``Initial URL`` to the page. ``Command`` is ignored for browser sessions. It needs
the iTerm2 browser plugin, which the CLI checks for before it gets here.

The only things this module does in iTerm2 are creating the dashboard tab (or a
window holding it), activating that tab, and closing a stale dashboard tab left by
a previous server (only if it is still a browser session). It never touches any
other session.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import iterm2

_log = logging.getLogger(__name__)


def browser_profile(url: str) -> iterm2.LocalWriteOnlyProfile:
    # LocalWriteOnlyProfile has no setter for "Initial URL"; raw keys are JSON-encoded by the class.
    return iterm2.LocalWriteOnlyProfile(values={"Custom Command": "Browser", "Initial URL": url})


async def _bring_forward(app, session) -> None:
    await session.async_activate(select_tab=True, order_window_front=True)
    await app.async_activate(raise_all_windows=False, ignoring_other_apps=True)


def _ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


async def _close_if_browser(session) -> None:
    """Close a previous server's dashboard tab, but only if it is still a browser session."""
    sid = session.session_id
    try:
        job = await session.async_get_variable("jobName")
    except Exception as exc:
        _log.warning("stale dashboard %s left alone: jobName unreadable (%s)", sid, type(exc).__name__)
        return
    if job is not None:  # not a browser session, so not ours
        _log.warning("stale dashboard id %s is not a browser session; left alone", sid)
        return
    try:
        await session.async_close(force=True)
    except Exception as exc:
        _log.warning("closing stale dashboard %s failed: %s", sid, type(exc).__name__)
        return
    _log.info("closed stale dashboard %s", sid)


def _first_session(tab):
    if tab is None:
        return None
    return tab.current_session or next(iter(tab.sessions), None)


async def open_dashboard(
    connection,
    url: str,
    existing_session_id: str | None,
    *,
    stale_session_id: str | None = None,
    on_created: Callable[[str], None] | None = None,
) -> str | None:
    """The dashboard's session id: the still-open tab brought forward, or a new browser tab.

    ``existing_session_id`` is this server's own tab. ``stale_session_id`` is a previous
    server's tab (persisted in the state dir). Its page points at a dead port and token,
    so when a new tab is opened the stale one is closed, if it is still a browser session.

    ``on_created`` gets a new tab's session id as soon as it exists, before the stale tab
    is closed or the new one brought forward, so the caller can exclude and persist it
    even if a later step raises.

    Returns None only if iTerm2 created nothing (the session ended right away).
    """
    start = time.perf_counter()
    app = await iterm2.async_get_app(connection)
    if existing_session_id is not None:
        session = app.get_session_by_id(existing_session_id)
        if session is not None:
            await _bring_forward(app, session)
            _log.info("dashboard %s brought forward in %.0f ms", existing_session_id, _ms(start))
            return existing_session_id

    stale = app.get_session_by_id(stale_session_id) if stale_session_id is not None else None

    profile = browser_profile(url)
    window = app.current_terminal_window
    if window is not None:
        tab = await window.async_create_tab(profile_customizations=profile)
    else:  # no terminal windows open: the dashboard gets a window of its own
        new_window = await iterm2.Window.async_create(connection, profile_customizations=profile)
        tab = None
        if new_window is not None:
            tab = new_window.current_tab or next(iter(new_window.tabs), None)
    session = _first_session(tab)
    if session is None:
        _log.warning("dashboard tab was not created")
        return None  # any stale tab stays (still excluded) rather than leaving no dashboard
    if on_created is not None:
        on_created(session.session_id)
    if stale is not None:
        # Closed after the new tab exists: closing a window's last tab first could close the
        # window we're about to open into, or quit iTerm2 if it quits with its last window.
        await _close_if_browser(stale)
    await _bring_forward(app, session)
    _log.info("dashboard opened in session %s in %.0f ms", session.session_id, _ms(start))
    return session.session_id
