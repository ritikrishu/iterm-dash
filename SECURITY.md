# Security

## What iterm-dash exposes

The dashboard shows the live contents of your other iTerm2 sessions: whatever is on their screens, plus the status text iTerm2's Claude Code integration reports. Anyone who can load the dashboard page can read those screens. Protecting that page is the main security concern.

## How it's protected

- **Localhost only.** The server binds to `127.0.0.1` on a random port. It isn't reachable from other machines.
- **A token on every dynamic route.** Each server run generates a random token. The page, the WebSocket and the local API all reject requests without it, and the token is compared in constant time. The page's static JavaScript and CSS under `/static/` are served without a token; they contain no terminal data.
- **Origin check.** The WebSocket refuses connections that send an `Origin` other than the server's own `http://127.0.0.1:<port>` page. A client that sends no `Origin` still needs the token.
- **Private state files.** `server.json` (port and token) is written with mode `0600` inside a `0700` folder in `~/Library/Application Support/iterm-dash/`.
- **No default browser.** The dashboard opens only as an iTerm2 browser tab, so your default browser's extensions, history sync and session restore never see the token URL or terminal contents.
- **Read-only toward your sessions.** The only action iterm-dash takes on another session is focusing it. It never types into, resizes or injects input into a session.
- **No outbound network traffic.** iterm-dash talks only to iTerm2's local Python API and to its own server on `127.0.0.1`. It has no telemetry and no update checks, the page loads nothing from the internet, and the CLI's requests to the local server bypass any configured HTTP proxy.
- **Metadata-only logs.** `server.log` records ids, sizes, counts and timings, never screen text.

The token protects against other users and web pages, not against code already running as your user account. Such code can read `server.json`, and it can already use iTerm2's API directly.

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | Yes |

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub: [open a security advisory](https://github.com/ritikrishu/iterm-dash/security/advisories/new). Don't open a public issue for a security problem.

Include what you found, how to reproduce it, and the iterm-dash, iTerm2 and macOS versions you used. Leave terminal contents out of the report.
