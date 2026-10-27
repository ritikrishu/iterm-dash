"""The ``iterm-dash`` command: ``iterm-dash [stop|smoke|demo]``.

``iterm-dash`` makes sure the background server is running (detached, so it
outlives this shell), then asks it to open the dashboard as an iTerm2 browser
tab, or to bring the open one forward. It never opens the default browser.

The token goes only into request URLs sent to 127.0.0.1. It is never printed
or logged, except by ``iterm-dash demo``, whose job is to print the demo URL.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path

from iterm_dash.state import (
    ServerInfo,
    clear_server_info,
    pid_alive,
    read_server_info,
    state_dir,
)

PLUGIN_PATHS: tuple[Path, ...] = (
    Path("/Applications/iTermBrowserPlugin.app"),
    Path("~/Applications/iTermBrowserPlugin.app").expanduser(),
)

PLUGIN_MISSING = """\
iterm-dash shows its dashboard in an iTerm2 browser tab, which needs the
iTerm2 browser plugin. It isn't installed. To install it:

  1. Download https://iterm2.com/downloads/browser-plugin/iTermBrowserPlugin-1.0.zip
  2. Unzip it.
  3. Move iTermBrowserPlugin.app to /Applications.
  4. Run iterm-dash again.
"""

_POLL_SECONDS = 0.05
# A warm start answers in ~0.5 s, but the first run after install compiles bytecode (~4 s
# measured) and iTerm2 may ask the user to allow the API connection.
_START_TIMEOUT = 15.0
_HEALTH_TIMEOUT = 1.0
_OPEN_TIMEOUT = 15.0
_STOP_TIMEOUT = 2.0
_EXIT_WAIT = 3.0
_TERMINATE_WAIT = 2.0

# Never route 127.0.0.1 requests (which carry the token) through a configured proxy.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ServerStartError(RuntimeError):
    """The background server didn't become healthy in time."""


class _NotOurServer(Exception):
    """Nothing listens on the port, or whatever answers rejects our token."""


class _NoAnswer(Exception):
    """The server is there but didn't answer in time."""


def server_dir(mode: str) -> Path:
    """Where a mode's server keeps server.json and its log. Demo never shares the live dir."""
    d = state_dir()
    if mode == "demo":
        d = d / "demo"
        d.mkdir(mode=0o700, exist_ok=True)
    return d


def browser_plugin_installed(paths: Iterable[Path] | None = None) -> bool:
    return any(p.exists() for p in (PLUGIN_PATHS if paths is None else paths))


def dashboard_url(info: ServerInfo) -> str:
    return f"http://127.0.0.1:{info.port}/?token={info.token}"


def _call(info: ServerInfo, method: str, path: str, timeout: float) -> dict:
    url = f"http://127.0.0.1:{info.port}{path}?token={info.token}"
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    try:
        with _opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code >= 500:
            raise _NoAnswer(f"HTTP {exc.code}") from None
        raise _NotOurServer(f"HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            raise _NotOurServer("connection refused") from None
        raise _NoAnswer(type(exc.reason).__name__) from None
    except (TimeoutError, OSError, ValueError) as exc:
        raise _NoAnswer(type(exc).__name__) from None


def _healthy(info: ServerInfo, timeout: float = _HEALTH_TIMEOUT) -> bool:
    try:
        return _call(info, "GET", "/api/health", timeout).get("ok") is True
    except (_NotOurServer, _NoAnswer):
        return False


def _wait_for_exit(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while pid_alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL_SECONDS)
    return True


def spawn_detached(argv: list[str], log_path: Path) -> subprocess.Popen:
    """Start the server in its own session so it outlives the shell; output goes to the log.

    The handle only lets ensure_server notice a server that dies during start-up.
    """
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "ab") as log:
        return subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            # Our own dir, so `python -m` never imports stray modules from the user's cwd.
            cwd=log_path.parent,
            start_new_session=True,
            close_fds=True,
        )


def ensure_server(
    mode: str,
    d: Path,
    *,
    spawn: Callable[[list[str], Path], subprocess.Popen | None] = spawn_detached,
    timeout: float = 5.0,
) -> ServerInfo:
    """A healthy server of this mode for state dir ``d``: the running one, or a fresh one."""
    lock_fd = os.open(d / "start.lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        # Two quick `iterm-dash` runs must not both spawn a server.
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        info = read_server_info(d)
        if info is not None and pid_alive(info.pid) and _healthy(info):
            if info.mode == mode:
                return info
            _stop_server(info)  # a server of the other mode owns this dir
        if info is not None:
            clear_server_info(d, info.pid)

        argv = [sys.executable, "-m", "iterm_dash.server"] + (["--demo"] if mode == "demo" else [])
        proc = spawn(argv, d / "server.log")
        try:
            return _wait_until_healthy(mode, d, proc, timeout)
        except BaseException:
            # A start we gave up on (timeout, early exit, Ctrl-C) must not linger as a second,
            # unreachable server that a later run could adopt or overwrite.
            if proc is not None:
                _terminate(proc, d)
            raise
    finally:
        os.close(lock_fd)  # also releases the lock


def _wait_until_healthy(mode: str, d: Path, proc: subprocess.Popen | None, timeout: float) -> ServerInfo:
    """Poll server.json until our server answers. With a handle, only our own child counts."""
    deadline = time.monotonic() + timeout
    while True:
        fresh = read_server_info(d)
        if (
            fresh is not None
            and fresh.mode == mode
            and (proc is None or fresh.pid == proc.pid)
            and pid_alive(fresh.pid)
            and _healthy(fresh)
        ):
            return fresh
        if proc is not None and proc.poll() is not None:
            raise ServerStartError(f"the background server exited during start-up (code {proc.returncode})")
        if time.monotonic() >= deadline:
            raise ServerStartError(f"the background server didn't start within {timeout:g} s")
        time.sleep(_POLL_SECONDS)


def _terminate(proc: subprocess.Popen, d: Path) -> None:
    """End a child this CLI spawned. We hold its handle, so its pid can't have been reused."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=_TERMINATE_WAIT)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=_TERMINATE_WAIT)
            except subprocess.TimeoutExpired:
                pass
    clear_server_info(d, proc.pid)  # only if it got as far as writing its own record


def _stop_server(info: ServerInfo) -> str:
    """Ask a server to stop and wait for its pid to exit: 'stopped', 'gone' or 'stuck'."""
    try:
        _call(info, "POST", "/api/stop", _STOP_TIMEOUT)
    except _NotOurServer:
        return "gone"
    except _NoAnswer:
        return "stuck"
    return "stopped" if _wait_for_exit(info.pid, _EXIT_WAIT) else "stuck"


def _cmd_open() -> int:
    if not browser_plugin_installed():
        print(PLUGIN_MISSING, end="", file=sys.stderr)
        return 1
    d = server_dir("live")
    log = d / "server.log"
    try:
        info = ensure_server("live", d, timeout=_START_TIMEOUT)
    except ServerStartError as exc:
        print(
            f"iterm-dash: {exc}. Check that iTerm2 is running with its Python API enabled "
            f"(Settings › General › Magic), then run iterm-dash again. Log: {log}",
            file=sys.stderr,
        )
        return 1
    try:
        _call(info, "POST", "/api/open", _OPEN_TIMEOUT)
    except (_NotOurServer, _NoAnswer) as exc:
        print(f"iterm-dash: couldn't open the dashboard ({exc}). Log: {log}", file=sys.stderr)
        return 1
    print(f"iterm-dash: dashboard open (server pid {info.pid})")
    return 0


def _cmd_stop() -> int:
    stopped, failed = False, False
    for label, d in (("iterm-dash", state_dir()), ("iterm-dash demo", state_dir() / "demo")):
        info = read_server_info(d)
        if info is None:
            continue
        if not pid_alive(info.pid):
            clear_server_info(d, info.pid)
            continue
        result = _stop_server(info)
        if result == "stopped":
            print(f"{label} stopped (pid {info.pid})")
            stopped = True
        elif result == "gone":
            clear_server_info(d, info.pid)
        else:
            print(f"{label}: the server (pid {info.pid}) didn't stop within {_EXIT_WAIT:g} s", file=sys.stderr)
            failed = True
    if not stopped and not failed:
        print("iterm-dash is not running")
    return 1 if failed else 0


def _cmd_smoke() -> int:
    from iterm_dash import smoke

    smoke.main()
    return 0


def _cmd_demo() -> int:
    d = server_dir("demo")
    try:
        info = ensure_server("demo", d, timeout=_START_TIMEOUT)
    except ServerStartError as exc:
        print(f"iterm-dash demo: {exc}. Log: {d / 'server.log'}", file=sys.stderr)
        return 1
    print(dashboard_url(info))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="iterm-dash",
        description="Live previews of your Claude Code sessions in an iTerm2 tab.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("stop", "smoke", "demo"),
        help="stop: stop the background server; smoke: list sessions (metadata only); "
        "demo: start a demo server and print its URL",
    )
    args = parser.parse_args(argv)
    if args.command == "stop":
        return _cmd_stop()
    if args.command == "smoke":
        return _cmd_smoke()
    if args.command == "demo":
        return _cmd_demo()
    return _cmd_open()


if __name__ == "__main__":
    sys.exit(main())
