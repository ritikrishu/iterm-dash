# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-10-28

### Changed

- iterm-dash is on PyPI: install it with `uv tool install iterm-dash` or `pipx install iterm-dash`, and update with `uv tool upgrade iterm-dash`.
- The README opens with a recording of the dashboard in a real iTerm2 window, including `n` and `2` switching tabs.
- README images and links are absolute, so they also show on PyPI.

### Added

- A release workflow publishes version tags to PyPI through Trusted Publishing, with PEP 740 attestations.

## [0.1.0] - 2026-10-27

First release.

### Added

- A live grid of your iTerm2 sessions in an iTerm2 browser tab. Each cell shows the session's full-width screen, scaled to fit, in the session's own colours, and keeps the latest output and input box in view.
- Claude Code status for every cell, read from iTerm2's Claude Code integration: "Needs your input" (the loudest), "Error", "Done · your turn" and "Working", plus the status detail, such as the question Claude is asking.
- Time since each session's status last changed (counted from when the dashboard saw the change), so a stale or stuck session stands out.
- The page title counts the visible sessions that need you, for example `(3) iterm-dash`.
- Keyboard navigation:
  - <kbd>n</kbd> jumps to the next session that needs you: waiting first, then errors, then finished turns.
  - <kbd>1</kbd>–<kbd>9</kbd> jump straight to that cell's tab.
  - Arrow keys move the focus ring; <kbd>Enter</kbd> jumps to the focused cell's tab.
  - <kbd>Space</kbd> enlarges the focused cell; <kbd>Space</kbd> or <kbd>Esc</kbd> closes it.
  - <kbd>h</kbd> hides the focused cell and <kbd>H</kbd> opens the "Hidden by you" menu to bring one back.
  - <kbd>a</kbd> toggles between Claude sessions only and all sessions.
  - <kbd>-</kbd> and <kbd>+</kbd> change the minimum text size.
  - <kbd>?</kbd> shows the shortcut list.
- Mouse support: click a cell to enlarge it, double-click to jump to its tab.
- Cells keep their order; new sessions are added at the end.
- The `iterm-dash` command starts the background server if needed and opens the dashboard tab, or brings the open one forward.
- `iterm-dash stop` stops the server, `iterm-dash smoke` prints a metadata-only check of your sessions, and `iterm-dash demo` serves a dashboard of made-up sessions.
- The server binds to `127.0.0.1`, requires a per-run token, and never changes your sessions; focusing a tab is its only action.

[Unreleased]: https://github.com/ritikrishu/iterm-dash/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/ritikrishu/iterm-dash/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/ritikrishu/iterm-dash/releases/tag/v0.1.0
