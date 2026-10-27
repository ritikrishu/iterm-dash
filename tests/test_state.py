import json
import os
import subprocess

from iterm_dash.state import (
    ServerInfo,
    Settings,
    clear_server_info,
    load_settings,
    pid_alive,
    read_server_info,
    save_settings,
    state_dir,
    write_server_info,
)


def test_server_info_roundtrip_and_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("ITERM_DASH_STATE_DIR", str(tmp_path / "s"))
    d = state_dir()
    info = ServerInfo(pid=123, port=8765, token="secret", mode="live")
    write_server_info(info, d)
    assert read_server_info(d) == info
    assert (d / "server.json").stat().st_mode & 0o777 == 0o600
    assert d.stat().st_mode & 0o777 == 0o700


def test_corrupt_server_info_reads_as_none(tmp_path, monkeypatch):
    monkeypatch.setenv("ITERM_DASH_STATE_DIR", str(tmp_path))
    d = state_dir()
    assert read_server_info(d) is None
    (d / "server.json").write_text("{not json")
    assert read_server_info(d) is None
    (d / "server.json").write_text(json.dumps({"pid": 1}))
    assert read_server_info(d) is None


def test_clear_only_removes_own_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("ITERM_DASH_STATE_DIR", str(tmp_path))
    d = state_dir()
    write_server_info(ServerInfo(pid=10, port=1, token="t", mode="demo"), d)
    clear_server_info(d, 11)
    assert (d / "server.json").exists()
    clear_server_info(d, 10)
    assert not (d / "server.json").exists()
    clear_server_info(d, 10)  # missing file is fine


def test_pid_alive():
    assert pid_alive(os.getpid())
    p = subprocess.Popen(["true"])
    p.wait()
    assert not pid_alive(p.pid)


def test_settings_defaults_on_missing_or_corrupt_and_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("ITERM_DASH_STATE_DIR", str(tmp_path))
    d = state_dir()
    assert load_settings(d) == Settings()
    (d / "settings.json").write_text("garbage")
    assert load_settings(d) == Settings()
    save_settings(Settings(show_all=True, min_font_px=9.5), d)
    assert load_settings(d) == Settings(show_all=True, min_font_px=9.5)


def test_pid_alive_rejects_non_positive():
    assert not pid_alive(0)
    assert not pid_alive(-1)


def test_writes_create_missing_directory(tmp_path):
    d = tmp_path / "missing" / "dir"
    write_server_info(ServerInfo(pid=1, port=2, token="t", mode="live"), d)
    assert read_server_info(d) is not None
    d2 = tmp_path / "other" / "dir"
    save_settings(Settings(show_all=True), d2)
    assert load_settings(d2).show_all is True
