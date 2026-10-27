"""Background server state files."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

_DEFAULT_DIR = "~/Library/Application Support/iterm-dash"


def state_dir() -> Path:
    d = Path(os.environ.get("ITERM_DASH_STATE_DIR") or _DEFAULT_DIR).expanduser()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


@dataclass
class ServerInfo:
    pid: int
    port: int
    token: str
    mode: str  # "live" or "demo"


@dataclass
class Settings:
    show_all: bool = False
    min_font_px: float = 7.0


def _write_private(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_server_info(info: ServerInfo, d: Path) -> None:
    _write_private(d / "server.json", json.dumps(asdict(info)), 0o600)


def read_server_info(d: Path) -> ServerInfo | None:
    try:
        data = json.loads((d / "server.json").read_text())
        return ServerInfo(
            pid=int(data["pid"]),
            port=int(data["port"]),
            token=str(data["token"]),
            mode=str(data["mode"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def clear_server_info(d: Path, pid: int) -> None:
    info = read_server_info(d)
    if info is not None and info.pid == pid:
        (d / "server.json").unlink(missing_ok=True)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def load_settings(d: Path) -> Settings:
    try:
        data = json.loads((d / "settings.json").read_text())
        return Settings(
            show_all=bool(data.get("show_all", False)),
            min_font_px=float(data.get("min_font_px", 7.0)),
        )
    except (OSError, ValueError, AttributeError, TypeError):
        return Settings()


def save_settings(s: Settings, d: Path) -> None:
    _write_private(d / "settings.json", json.dumps(asdict(s)), 0o644)
