"""scripts/tool_eval.py, offline: a scripted fake model stands in for the API."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("tool_eval", ROOT / "scripts" / "tool_eval.py")
te = importlib.util.module_from_spec(_spec)
sys.modules["tool_eval"] = te
_spec.loader.exec_module(te)


@pytest.fixture(scope="module")
def toolset(tmp_path_factory):
    from backend.config import settings
    saved = settings.data_dir, settings.skills_dir, settings.db_path
    yield te.Toolset(tmp_path_factory.mktemp("eval"))
    settings.data_dir, settings.skills_dir, settings.db_path = saved


@pytest.fixture(scope="module")
def cases():
    return {c["id"]: c for c in te.load_cases(te.DEFAULT_CASES)}


class Fake:
    """Replies with scripted tool calls, one list per model call; records what it saw."""

    def __init__(self, *replies, usage=100):
        self.replies = list(replies)
        self.seen = []
        self.usage = usage

    def __call__(self, messages, tools):
        self.seen.append({"messages": list(messages), "tools": tools})
        calls = self.replies.pop(0) if self.replies else []
        return {"tool_calls": [{"id": f"c{i}", "name": n, "args": a}
                               for i, (n, a) in enumerate(calls)],
                "content": None, "usage": {"prompt_tokens": self.usage, "completion_tokens": 10}}


def names(tools):
    return [t["function"]["name"] for t in tools]


def test_case_file_is_wellformed(cases):
    assert 38 <= len(cases) <= 46
    assert sum(1 for c in cases.values() if c.get("chat") == "local") == 2
    assert sum(1 for c in cases.values() if len(c.get("preload") or []) == 4) == 4


def test_every_expected_tool_is_granted(cases, toolset):
    for c in cases.values():
        view = te.present(c, toolset.specs(c.get("chat") == "local", bool(c.get("skills"))))
        assert c["expect"]["tool"] in view.granted(), c["id"]


def test_correct_direct_call(cases, toolset):
    m = Fake([("read_file", {"path": "backend/main.py"})])
    r = te.run_case(cases["read-file"], toolset.specs(), m)
    assert r["correct"] and r["hops"] == 0 and r["error"] is None
    assert r["first_call"] == "read_file" and r["prompt_tokens"] == 100
    assert r["wire_tools"] == len(m.seen[0]["tools"]) and len(m.seen) == 1


def test_correct_after_one_hop(cases, toolset):
    c = cases["project-crawl"]
    m = Fake([("tools", {"section": "project"})], [("crawl_codebase", {})])
    r = te.run_case(c, toolset.specs(), m)
    assert r["correct"] and r["hops"] == 1
    assert r["first_call"] == "tools(project)"
    assert r["prompt_tokens"] == 200 and len(m.seen) == 2
    tool_msgs = [x for x in m.seen[1]["messages"] if x["role"] == "tool"]
    assert tool_msgs and "section 'project'" in tool_msgs[0]["content"]
    assert m.seen[1]["messages"][-2]["tool_calls"][0]["function"]["name"] == "tools"


def test_hop_then_wrong_tool_is_wrong(cases, toolset):
    m = Fake([("tools", {"section": "project"})], [("journal_update", {})])
    r = te.run_case(cases["project-crawl"], toolset.specs(), m)
    assert not r["correct"] and r["hops"] == 1 and r["chosen"] == "journal_update"


def test_wrong_tool_and_no_call(cases, toolset):
    r = te.run_case(cases["read-file"], toolset.specs(), Fake([("list_files", {})]))
    assert not r["correct"] and r["chosen"] == "list_files" and r["error"] is None
    r = te.run_case(cases["read-file"], toolset.specs(), Fake([]))
    assert not r["correct"] and r["first_call"] is None and r["chosen"] is None
    r = te.run_case(cases["read-file"], toolset.specs(), Fake([("no_such_tool", {})]))
    assert not r["correct"]


def test_expected_action_is_checked(toolset):
    case = {"id": "x", "prompt": "hi", "expect": {"tool": "read_file", "action": "go"}}
    r = te.run_case(case, toolset.specs(), Fake([("read_file", {"action": "stop"})]))
    assert not r["correct"]
    r = te.run_case(case, toolset.specs(), Fake([("read_file", {"action": "go"})]))
    assert r["correct"]


def test_model_error_is_recorded(cases, toolset):
    def boom(messages, tools):
        raise RuntimeError("HTTP 500")
    r = te.run_case(cases["read-file"], toolset.specs(), boom)
    assert not r["correct"] and "HTTP 500" in r["error"]


def test_preload_loads_sections_before_the_call(cases, toolset):
    c = cases["ceiling-agents"]
    m = Fake([("spawn_agent", {"task": "x"})])
    r = te.run_case(c, toolset.specs(), m)
    wire = names(m.seen[0]["tools"])
    for sec_tool in ("browser_click", "desk_click", "git_status", "service_logs"):
        assert any(sec_tool == n or n in ("browser", "desk", "git", "services") for n in wire)
    assert "spawn_agent" not in wire and not any(n == "agents" for n in wire)
    assert r["correct"] and r["hops"] == 0     # a direct call by name still resolves
    plain = te.run_case(cases["read-file"], toolset.specs(), Fake([]))
    assert r["wire_tools"] > plain["wire_tools"]


def test_ceiling_targets_are_not_on_the_wire(cases, toolset):
    ceiling = [c for c in cases.values() if len(c.get("preload") or []) == 4]
    for c in ceiling:
        view = te.present(c, toolset.specs())
        unit = view.member_of.get(c["expect"]["tool"], (c["expect"]["tool"],))[0]
        assert unit not in names(view.wire()), c["id"]


def test_local_case_tool_filtering(toolset):
    local = {s["function"]["name"] for s in toolset.specs(local=True)}
    plain = {s["function"]["name"] for s in toolset.specs(local=False)}
    from backend import localexec
    assert "local_read_file" in local and "local_shell" in local
    assert not (local & localexec._SANDBOX_TOOLS)
    assert "read_file" in plain and "run_code" in plain
    assert not {n for n in plain if n.startswith("local_")}


def test_skills_granted_only_for_skill_cases(toolset):
    with_skills = {s["function"]["name"] for s in toolset.specs(skills=True)}
    without = {s["function"]["name"] for s in toolset.specs(skills=False)}
    assert {"weather", "github", "release-notes"} <= with_skills
    assert not ({"weather", "github", "release-notes"} & without)


def rec(i, ok, hops=0, wire=10, tok=100, comp=0):
    return {"id": i, "correct": ok, "hops": hops, "wire_tools": wire, "prompt_tokens": tok,
            "completion_tokens": comp, "latency_s": 1.0, "error": None, "first_call": "x"}


def test_summary_maths():
    s = te.summarize([rec("a", True), rec("b", True, hops=1, wire=12, tok=300),
                      rec("c", False, wire=14, tok=200, comp=1000), rec("d", False)],
                     price={"in": 1.0, "out": 2.0})
    assert s["cases"] == 4 and s["accuracy"] == 0.5 and s["accuracy_no_hop"] == 0.25
    assert s["mean_hops"] == 0.25 and s["mean_wire_tools"] == 11.5
    assert s["mean_prompt_tokens"] == 175
    assert s["est_cost_usd"] == pytest.approx((700 * 1.0 + 1000 * 2.0) / 1e6)
    assert "accuracy" in te.format_summary(s, "x")


def test_compare_lists_differences(tmp_path, capsys):
    a = [rec("same", True), rec("fix", False), rec("break", True)]
    b = [rec("same", True), rec("fix", True, hops=1), rec("break", False)]
    pa, pb = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    pa.write_text("\n".join(json.dumps(r) for r in a))
    pb.write_text("\n".join(json.dumps(r) for r in b))
    assert te.main(["--compare", str(pa), str(pb)]) == 0
    out = capsys.readouterr().out
    assert "fix: FIXED" in out and "break: BROKE" in out and "same:" not in out


def test_main_writes_jsonl_with_repeat(tmp_path, capsys):
    out = tmp_path / "run.jsonl"
    m = Fake(*[[("read_file", {})]] * 4)
    assert te.main(["--only", "read-file,list-files", "--repeat", "2", "--out", str(out)],
                   model=m) == 0
    rows = te.read_run(out)
    assert [r["id"] for r in rows] == ["read-file", "list-files"] * 2
    assert [r["correct"] for r in rows] == [True, False, True, False]
    assert "accuracy" in capsys.readouterr().out


def test_dry_run_makes_no_model_call(capsys):
    def boom(*a, **k):
        raise AssertionError("model called in a dry run")
    assert te.main(["--dry-run", "--limit", "5"], model=boom) == 0
    out = capsys.readouterr().out
    assert "on wire" in out and "read-file" in out and "mean" in out


def _snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, p.stat().st_size)
            for p in sorted(root.rglob("*"))}


def test_dry_run_never_writes_the_state_dir(tmp_path):
    state = tmp_path / "state"
    for sub in ("data", "skills", "memory", "agents", "projects"):
        (state / sub).mkdir(parents=True)
    (state / "data" / "marker.txt").write_text("x")
    (state / "skills" / "marker.txt").write_text("x")
    before = _snapshot(state)
    env = {**os.environ, "JARVIS_STATE_DIR": str(state), "HOME": str(tmp_path / "home")}
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "tool_eval.py"), "--dry-run",
                        "--only", "read-file,skill-weather,local-read"],
                       capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)
    assert r.returncode == 0, r.stderr[-500:]
    assert "skill-weather" in r.stdout
    assert _snapshot(state) == before
    assert not (tmp_path / "home").exists() or not list((tmp_path / "home").rglob("*.db"))
