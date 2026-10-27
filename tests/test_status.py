from iterm_dash.status import StatusTracker, StatusView, classify


def test_waiting():
    v = classify({"status": "waiting", "detail": "Which index?", "dot_color": "#5f87ff"}, is_claude=True, job="2.1.310")
    assert (v.tone, v.label, v.color, v.detail, v.needs_you) == ("waiting", "Needs your input", "#5f87ff", "Which index?", True)


def test_idle_is_done():
    v = classify({"status": "idle", "dot_color": "#00d75f"}, is_claude=True, job="x")
    assert (v.tone, v.label, v.color, v.needs_you) == ("done", "Done · your turn", "#00d75f", True)


def test_red_dot_or_error_text_is_error_even_when_idle():
    assert classify({"status": "idle", "dot_color": "#FF3B30"}, is_claude=True, job="x").tone == "error"
    v = classify({"status": "error"}, is_claude=True, job="x")
    assert (v.tone, v.label, v.color) == ("error", "Error", "#ff3b30")


def test_working_is_quiet():
    v = classify({"status": "working", "detail": "4 background tasks running"}, is_claude=True, job="x")
    assert (v.tone, v.label, v.color, v.needs_you) == ("working", "Working", "#ff9500", False)


def test_unknown_text_passes_through_with_its_dot_color():
    v = classify({"status": "compacting", "dot_color": "#abcdef"}, is_claude=True, job="x")
    assert (v.tone, v.label, v.color, v.needs_you) == ("other", "compacting", "#abcdef", False)


def test_missing_or_malformed_status():
    for raw in (None, {}, {"status": None}, {"status": 3}):
        assert classify(raw, is_claude=True, job="2.1.310") == StatusView("unknown", "Status unknown", "#888888", None)
    assert classify(None, is_claude=False, job="caffeinate") == StatusView("shell", "caffeinate", "#888888", None)
    assert classify(None, is_claude=False, job=None).label == "shell"


def test_tracker_reports_changes_with_timestamps():
    t = iter([10.0, 11.0, 12.0, 13.0])
    tr = StatusTracker(clock=lambda: next(t))
    w, d = classify({"status": "working"}, is_claude=True, job="x"), classify({"status": "idle"}, is_claude=True, job="x")
    assert tr.update("A", w) == (True, 10.0)
    assert tr.update("A", w) == (False, 10.0)
    assert tr.update("A", d) == (True, 12.0)
    tr.forget("A")
    assert tr.update("A", d) == (True, 13.0)
