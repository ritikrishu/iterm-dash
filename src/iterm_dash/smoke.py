"""Read-only smoke check against the real iTerm2: ``python -m iterm_dash.smoke``.

Prints one metadata line per session (id, title, size, status, fetch time,
non-blank row count). It never prints screen text or status detail.
"""

from __future__ import annotations

import sys
import time

import iterm2

from iterm_dash.backend import ItermBackend, has_claude_status, read_status
from iterm_dash.registry import SessionInfo, list_sessions

_MAX_TITLE = 40
_MAX_STATUS = 24


def format_line(info: SessionInfo, raw_status: dict | None, fetch_ms: float, nonblank_rows: int) -> str:
    status = raw_status.get("status") if isinstance(raw_status, dict) else None
    status_text = status[:_MAX_STATUS] if isinstance(status, str) and status else "-"
    title = info.title if len(info.title) <= _MAX_TITLE else info.title[: _MAX_TITLE - 1] + "…"
    kind = "claude" if info.is_claude else "other"
    fetch = f"{fetch_ms:.1f}ms" if fetch_ms >= 0 else "failed"
    return (
        f"{info.id}  {title!r}  {info.cols}x{info.rows}  {kind}  status={status_text}  "
        f"fetch={fetch}  nonblank_rows={nonblank_rows}"
    )


async def run(connection) -> list[str]:
    app = await iterm2.async_get_app(connection)
    backend = ItermBackend(connection, None, app=app)

    async def has_status(sid: str) -> bool:
        return has_claude_status(await read_status(connection, sid))

    lines = []
    for info in await list_sessions(app, (), has_status):
        raw = await read_status(connection, info.id)
        session = app.get_session_by_id(info.id)
        fetch_ms, nonblank = -1.0, 0
        if session is not None:
            try:
                start = time.perf_counter()
                await session.async_get_screen_contents()
                fetch_ms = (time.perf_counter() - start) * 1000
            except Exception:
                pass
            # Full backend path (palette + render); only the row count leaves here.
            frame = await backend.fetch_frame(session)
            if frame is not None:
                nonblank = sum(1 for row in frame.lines if any(run[0].strip() for run in row))
        lines.append(format_line(info, raw, fetch_ms, nonblank))
    return lines


def main() -> None:
    async def go(connection) -> None:
        try:
            for line in await run(connection):
                print(line)
        except Exception as exc:
            print(f"smoke failed: {type(exc).__name__}", file=sys.stderr)

    iterm2.run_until_complete(go)


if __name__ == "__main__":
    main()
