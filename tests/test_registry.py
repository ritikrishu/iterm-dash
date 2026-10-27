import asyncio
from types import SimpleNamespace

from iterm_dash.registry import display_title, is_claude_session, list_sessions


def fake_session(sid):
    values = {"autoName": f"title {sid}", "jobName": "zsh", "commandLine": "-zsh"}

    async def get_var(name):
        return values.get(name)

    return SimpleNamespace(
        session_id=sid,
        grid_size=SimpleNamespace(width=214, height=57),
        async_get_variable=get_var,
    )


def fake_app(windows):
    return SimpleNamespace(
        terminal_windows=[
            SimpleNamespace(
                tabs=[SimpleNamespace(sessions=[fake_session(s) for s in tab]) for tab in tabs]
            )
            for tabs in windows
        ]
    )


def always(v):
    async def f(_sid):
        return v

    return f


def test_is_claude_session():
    assert is_claude_session("2.1.310", "claude --dangerously-skip-permissions", False)
    assert is_claude_session("node", "/usr/local/bin/claude", False)
    assert is_claude_session("zsh", None, True)
    assert not is_claude_session("caffeinate", "caffeinate -dimsu", False)
    assert not is_claude_session("zsh", "-zsh", False)


def test_display_title():
    assert display_title("Server DB layer optimisation epic", "Server DB layer optimisation epic (claude)", "2.1.310") == "Server DB layer optimisation epic"
    assert display_title(None, "Foo (claude)", "2.1.310") == "Foo"
    assert display_title(None, None, "zsh") == "zsh"
    assert display_title(None, None, None) == "session"


def test_list_sessions_keeps_window_tab_order_and_excludes_dashboard():
    app = fake_app([[["A", "B"], ["C"]], [["DASH"], ["D"]]])
    infos = asyncio.run(list_sessions(app, exclude={"DASH"}, has_status=always(False)))
    assert [i.id for i in infos] == ["A", "B", "C", "D"]
    assert infos[0].cols == 214 and infos[0].rows == 57
    assert not infos[0].is_claude


def test_list_sessions_skips_broken_sessions():
    app = fake_app([[["A", "BAD_VAR", "B", "BAD_SIZE", "C"]]])
    sessions = app.terminal_windows[0].tabs[0].sessions

    async def boom(name):
        raise RuntimeError("secret value")

    sessions[1].async_get_variable = boom
    sessions[3].grid_size = None
    infos = asyncio.run(list_sessions(app, exclude=set(), has_status=always(False)))
    assert [i.id for i in infos] == ["A", "B", "C"]
