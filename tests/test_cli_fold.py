"""Tool steps fold into one line (clients/jav3cli/jav3): a live line with step,
time and tokens while the turn runs, a summary after, ctrl+o to open every step,
and the ▣ footer with ↑ ↓ and tok/s from the server's `usage` events."""
import pytest

from test_cli_running import Feed, _plain, _start, _until, jav3


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return tmp_path / "cfg" / "jav3"


# --- the text -----------------------------------------------------------------------------

def test_summary_lists_verbs_once_and_counts_failures():
    steps = [("search_codebase", {"query": "retry"}, True), ("read_file", {"path": "a"}, True),
             ("edit_file", {"path": "a"}, True), ("run_code", {"command": "pytest"}, True)]
    assert jav3.fold_summary(steps) == "4 steps · grep, read, edit, run"
    assert jav3.fold_summary(steps[:1]) == "1 step · grep"
    steps[3] = ("run_code", {"command": "pytest"}, False)
    assert jav3.fold_summary(steps) == "4 steps (1 failed) · grep, read, edit, run"
    # a repeated verb shows once; a tool without a verb of its own shows its name
    assert jav3.fold_summary([("read_file", {}, True)] * 3 + [("mystery", {}, True)]) \
        == "4 steps · read, mystery"


def test_summary_stops_at_five_verbs():
    names = ["read_file", "write_file", "edit_file", "run_code", "web_search", "list_files"]
    assert jav3.fold_summary([(n, {}, True) for n in names]) \
        == "6 steps · read, write, edit, run, search, …"


def test_live_text_with_and_without_tokens():
    assert jav3.fold_live_text("Grep", 3, 12) == "Grep · step 3 · 12s"
    assert jav3.fold_live_text("Grep", 3, 12, 18200, 640, 41.2) \
        == "Grep · step 3 · 12s · ↑ 18.2k ↓ 640 · 41 tok/s"


def test_turn_tokens_estimate_until_the_usage_event_corrects_them():
    t = jav3.TurnState()
    assert t.tokens() == (None, 0, None)          # an older server: no numbers
    t.usage({"in": 1000, "out": 100, "ms": 2000})
    assert t.tokens() == (1000, 100, 50.0)
    t.streamed("x" * 400)                          # 100 more tokens by chars / 4
    up, down, _ = t.tokens()
    assert (up, down) == (1000, 200)
    t.usage({"in": 1500, "out": 90, "ms": 1000})   # the real count replaces the estimate
    assert t.tokens() == (2500, 190, pytest.approx(190 / 3))


# --- on screen ----------------------------------------------------------------------------

def _steps(i0=0, n=4):
    names = [("search_codebase", {"query": "retry"}), ("read_file", {"path": "a.py"}),
             ("edit_file", {"path": "a.py", "find": "a", "replace": "b"}),
             ("run_code", {"command": "pytest"})]
    out = []
    for i in range(n):
        name, args = names[i]
        out.append({"type": "tool", "id": f"c{i0 + i}", "name": name, "args": args})
        out.append({"type": "tool_result", "id": f"c{i0 + i}", "name": name, "ok": True,
                    "result": "x"})
    return out


async def test_steps_fold_into_one_live_line_then_a_summary(cfg):
    pytest.importorskip("textual")
    feed = Feed()
    app = jav3.build_tui("http://h:1", "jvd_x", transport=feed.transport())
    async with app.run_test(size=(110, 30)) as pilot:
        await _start(pilot, app, feed)
        feed.send({"type": "usage", "in": 18200, "out": 640, "ms": 15000, "model": "m"},
                  *_steps(n=2))
        folds = lambda: list(app.query("FoldLine"))  # noqa: E731
        assert await _until(pilot, lambda: len(folds()) == 1 and len(app.query("ToolView")) == 2)
        await pilot.pause(0.3)
        line = _plain(folds()[0])
        assert "step 2" in line and "↑ 18.2k ↓ 640" in line and "tok/s" in line
        assert all(not tv.display for tv in app.query("ToolView"))      # folded away
        # a message ends the run: the line becomes a summary, and the next steps start another
        feed.send({"type": "token", "text": "Found it."})
        assert await _until(pilot, lambda: app.turn.seg == "Found it." and len(app.query("Reply")))
        await pilot.pause(0.2)
        assert _plain(folds()[0]).startswith("▸ 2 steps · grep, read")
        feed.send({"type": "tool", "id": "z1", "name": "run_code", "args": {"command": "ls"}})
        assert await _until(pilot, lambda: len(folds()) == 2)
        # ctrl+o opens every fold, and again folds them
        await pilot.press("ctrl+o")
        await pilot.pause(0.2)
        assert all(tv.display for tv in app.query("ToolView"))
        assert all(not f.display for f in folds())
        await pilot.press("ctrl+o")
        await pilot.pause(0.2)
        assert all(f.display for f in folds()) and not any(tv.display for tv in app.query("ToolView"))
        feed.send({"type": "final", "content": "ok", "conversation_id": 9})
        assert await _until(pilot, lambda: not app.busy)


async def test_footer_shows_tokens_and_failures_show_in_the_summary(cfg):
    pytest.importorskip("textual")
    feed = Feed()
    app = jav3.build_tui("http://h:1", "jvd_x", transport=feed.transport())
    async with app.run_test(size=(110, 30)) as pilot:
        await _start(pilot, app, feed)
        evs = _steps(n=4)
        evs[-1] = {"type": "tool_result", "id": "c3", "name": "run_code", "ok": False,
                   "result": "error: boom"}
        feed.send(*evs, {"type": "usage", "in": 52100, "out": 1900, "ms": 50000, "model": "m"},
                  {"type": "token", "text": "Done."},
                  {"type": "final", "content": "Done.", "conversation_id": 9})
        assert await _until(pilot, lambda: not app.busy)
        await pilot.pause(0.2)
        assert _plain(app.query_one("FoldLine")).startswith("▸ 4 steps (1 failed)")
        foot = _plain(list(app.query("Footer"))[-1])
        assert "↑ 52.1k ↓ 1.9k" in foot and "38 tok/s" in foot and "4 tools" in foot


async def test_no_usage_events_means_no_token_numbers(cfg):
    pytest.importorskip("textual")
    feed = Feed()
    app = jav3.build_tui("http://h:1", "jvd_x", transport=feed.transport())
    async with app.run_test(size=(110, 30)) as pilot:
        await _start(pilot, app, feed)
        feed.send(*_steps(n=1), {"type": "final", "content": "ok", "conversation_id": 9})
        assert await _until(pilot, lambda: not app.busy)
        await pilot.pause(0.2)
        foot = _plain(list(app.query("Footer"))[-1])
        assert "↑" not in foot and "tok/s" not in foot and "1 tool" in foot
