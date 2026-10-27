# Contributing

Thanks for taking a look. Bug reports, small fixes and ideas are all welcome. For anything larger than a small fix, please open an issue first so we can agree on the approach before you spend time on it.

## Setup

You need macOS, iTerm2 3.7 or later, Python 3.12+, [uv](https://docs.astral.sh/uv/) and Node.js (for the page's tests).

```sh
uv sync                              # install dependencies into .venv
uv tool install --editable .         # optional: put your checkout on PATH as `iterm-dash`
```

## Running the tests

```sh
uv run pytest                        # Python tests
npm test                             # page module tests (node --test tests/web/*.test.js)
```

One test at a time:

```sh
uv run pytest tests/test_render.py::test_wide_char_is_its_own_run_and_continuation_skipped -v
```

The tests don't need iTerm2. The backend tests run against fake sessions, and the page's logic lives in pure modules (`layout.js`, `nav.js`, `clicks.js`, `format.js`, `rows.js`) that run under `node --test`.

## Working on the page without iTerm2

```sh
uv run iterm-dash demo
```

This starts a demo server with made-up sessions in the background and prints its URL. Open it in any browser to work on the grid, the enlarged view and the shortcuts. The demo server keeps its state in a separate `demo/` folder and never talks to iTerm2. `iterm-dash stop` stops it.

## Checking against real sessions

```sh
uv run iterm-dash smoke
```

The smoke check connects to iTerm2 and prints one line of metadata per session (id, title, size, status, fetch time, row count). It changes nothing and never prints screen text.

## State and logs

The server keeps `server.json`, `settings.json`, `dashboard.json` and `server.log` in `~/Library/Application Support/iterm-dash/`. Set `ITERM_DASH_STATE_DIR` to use another folder. `iterm-dash stop` stops a running server.

## Project rules

These keep the tool safe to leave running next to real work:

- **Read-only toward sessions.** The only thing iterm-dash may do to another session is focus it (`async_activate`). Don't call `async_set_grid_size`, `async_send_text` or `async_inject` on a watched session.
- **Metadata only in logs and debug output.** Print ids, sizes, counts and timings, never screen text or status detail. Log exceptions by type name, because their messages can contain screen text.
- **Test changes on a throwaway session.** If you're experimenting with anything that changes a session, open a scratch tab for it rather than pointing it at your real work.
- **No network beyond 127.0.0.1.** The page loads nothing from the internet, and the server listens on localhost only.

## Commits and pull requests

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/), as in the existing history:

```
feat: show the session's detail line under its title
fix: keep the focus ring on the same cell after a session closes
docs: explain the line cache
chore: bump aiohttp
```

Keep pull requests focused on one change, add or update tests with it, and make sure `uv run pytest` and `npm test` pass.

## How it works

[docs/architecture.md](docs/architecture.md) walks through the modules, the preview pipeline, status, the security model and the iTerm2 behaviour the code depends on. It's worth reading before a change to `backend.py` or `render.py`.
