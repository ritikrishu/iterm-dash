"""Throttled, coalescing fetch pump (latest-only)."""

import asyncio
import logging
from typing import Awaitable, Callable

log = logging.getLogger(__name__)


class LatestOnlyPump:
    """Runs ``fetch`` at most once per ``min_interval`` and never concurrently.

    Any number of ``mark_dirty`` calls between fetches collapse into one more
    fetch, so the last change is never lost.
    """

    def __init__(self, fetch: Callable[[], Awaitable[None]], min_interval: float = 0.25):
        self._fetch = fetch
        self._min_interval = min_interval
        self._dirty = asyncio.Event()
        self._last_start: float | None = None

    def mark_dirty(self) -> None:
        self._dirty.set()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                await self._dirty.wait()
                if self._last_start is not None:
                    wait = self._last_start + self._min_interval - loop.time()
                    if wait > 0:
                        await asyncio.sleep(wait)
                # Clear just before fetching: marks during the fetch re-arm it.
                self._dirty.clear()
                self._last_start = loop.time()
                try:
                    await self._fetch()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("fetch failed: %s", type(exc).__name__)
        except asyncio.CancelledError:
            return
