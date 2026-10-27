"""Classify iTerm2 cc-status dicts into dashboard tones and track changes."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

Tone = Literal["waiting", "error", "done", "working", "other", "unknown", "shell"]

_GREY = "#888888"
_RED = "#ff3b30"
_ORANGE = "#ff9500"
_BLUE = "#5f87ff"
_GREEN = "#00d75f"


@dataclass(frozen=True)
class StatusView:
    tone: Tone
    label: str
    color: str | None
    detail: str | None

    @property
    def needs_you(self) -> bool:
        return self.tone in ("waiting", "error", "done")


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def classify(raw: dict | None, *, is_claude: bool, job: str | None) -> StatusView:
    status = raw.get("status") if isinstance(raw, dict) else None
    if not isinstance(status, str) or not status.strip():
        if is_claude:
            return StatusView("unknown", "Status unknown", _GREY, None)
        return StatusView("shell", job or "shell", _GREY, None)

    assert raw is not None
    text = status.strip()
    key = text.lower()
    dot = _str_or_none(raw.get("dot_color"))
    detail = _str_or_none(raw.get("detail"))

    if key == "waiting":
        return StatusView("waiting", "Needs your input", dot or _BLUE, detail)
    if key in ("error", "failed") or (dot is not None and dot.lower() == _RED):
        return StatusView("error", "Error", _RED, detail)
    if key in ("idle", "done"):
        return StatusView("done", "Done · your turn", dot or _GREEN, detail)
    if key == "working":
        return StatusView("working", "Working", dot or _ORANGE, detail)
    return StatusView("other", text, dot or _GREY, detail)


class StatusTracker:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._last: dict[str, tuple[tuple[str, str], float]] = {}

    def update(self, session_id: str, view: StatusView) -> tuple[bool, float]:
        now = self._clock()
        key = (view.tone, view.label)
        prev = self._last.get(session_id)
        if prev is not None and prev[0] == key:
            return False, prev[1]
        self._last[session_id] = (key, now)
        return True, now

    def forget(self, session_id: str) -> None:
        self._last.pop(session_id, None)
