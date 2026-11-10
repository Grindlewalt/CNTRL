"""The model's thinking in the terminal client: a row in the run's fold (hidden while
folded, shown with ctrl+o), a live line with the end of the thought, a summary that
counts it as time, and a retry that voids it."""
import pytest

from test_cli_running import Feed, _plain, _start, _until, jav3


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return tmp_path / "cfg" / "cntrl"


def test_live_text_puts_the_tail_last_and_fits_the_width():
    txt = "the crash should only trigger above a speed threshold so check the velocity first"
    out = jav3.think_live_text(14, 320, txt, 2, 18200, 640, 41.2, width=80)
    assert out.startswith("thinking 14s · 320 words · step 2 · ↑ 18.2k ↓ 640 · 41 tok/s")
    assert len(out) <= 80 and out.endswith("first") and " · …" in out
    short = jav3.think_live_text(3, 2, "a\nb  c", width=80)
    assert short == "thinking 3s · 2 words · a b c"


def test_summary_counts_thinking_as_time_not_a_step():
    steps = [("read_file", {"path": "a"}, True)]
    assert jav3.fold_summary(steps, 40) == "1 step · read · thought 40s"
    assert jav3.fold_summary([], 8) == "thought for 8s"
    assert jav3.fold_summary([], 0.4) == "thought briefly"      # not "thought for 0s"
    assert jav3.fold_summary(steps) == "1 step · read"


def test_streamed_thought_counts_into_the_down_estimate():
    t = jav3.TurnState()
    t.usage({"in": 10, "out": 0, "ms": 0})
    t.streamed("x" * 400)
    assert t.tokens()[1] == 100


async def test_thinking_row_live_line_summary_and_ctrl_o(cfg):
    pytest.importorskip("textual")
    feed = Feed()
    app = jav3.build_tui("http://h:1", "jvd_x", transport=feed.transport())
    async with app.run_test(size=(110, 30)) as pilot:
        await _start(pilot, app, feed)
        feed.send({"type": "usage", "in": 1000, "out": 10, "ms": 1000, "model": "m"},
                  {"type": "think", "text": "The crash should only trigger "},
                  {"type": "think", "text": "above a speed\nthreshold"})
        assert await _until(pilot, lambda: len(app.query("ThinkView")) == 1)
        await pilot.pause(0.3)
        fold = app.query("FoldLine").first()
        line = _plain(fold)
        assert "thinking" in line and "words" in line and line.endswith("threshold")
        tv = app.query("ThinkView").first()
        assert not tv.display and not tv.done
        feed.send({"type": "tool", "id": "a", "name": "read_file", "args": {"path": "g.js"}},
                  {"type": "tool_result", "id": "a", "name": "read_file", "ok": True,
                   "result": "x"},
                  {"type": "token", "text": "Done."})
        assert await _until(pilot, lambda: len(app.query("Reply")) == 1)
        await pilot.pause(0.2)
        assert tv.done and len(app.query("FoldLine")) == 1
        summ = _plain(app.query("FoldLine").first())
        assert summ.startswith("▸ 1 step · read · thought ")
        await pilot.press("ctrl+o")
        await pilot.pause(0.2)
        assert tv.display and "above a speed" in _plain(tv) and ("thought for" in _plain(tv) or "thought briefly" in _plain(tv))
        feed.send({"type": "final", "content": "ok", "conversation_id": 9})
        assert await _until(pilot, lambda: not app.busy)


async def test_thinking_without_tools_still_gets_a_fold(cfg):
    pytest.importorskip("textual")
    feed = Feed()
    app = jav3.build_tui("http://h:1", "jvd_x", transport=feed.transport())
    async with app.run_test(size=(110, 30)) as pilot:
        await _start(pilot, app, feed)
        feed.send({"type": "think", "text": "hmm"}, {"type": "token", "text": "Hi"})
        assert await _until(pilot, lambda: len(app.query("Reply")) == 1)
        await pilot.pause(0.2)
        assert _plain(app.query("FoldLine").first()).startswith(("▸ thought for ", "▸ thought briefly"))
        feed.send({"type": "final", "content": "Hi", "conversation_id": 9})
        assert await _until(pilot, lambda: not app.busy)


async def test_a_retry_voids_the_thinking(cfg):
    pytest.importorskip("textual")
    feed = Feed()
    app = jav3.build_tui("http://h:1", "jvd_x", transport=feed.transport())
    async with app.run_test(size=(110, 30)) as pilot:
        await _start(pilot, app, feed)
        feed.send({"type": "think", "text": "first try"})
        assert await _until(pilot, lambda: len(app.query("ThinkView")) == 1)
        feed.send({"type": "retry"})
        assert await _until(pilot, lambda: len(app.query("ThinkView")) == 0)
        assert len(app.query("FoldLine")) == 0
        feed.send({"type": "think", "text": "second try"})
        assert await _until(pilot, lambda: len(app.query("ThinkView")) == 1)
        assert app.query("ThinkView").first().text == "second try"
        feed.send({"type": "final", "content": "ok", "conversation_id": 9})
        assert await _until(pilot, lambda: not app.busy)
