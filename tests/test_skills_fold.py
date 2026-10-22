"""Installed skills reach the model as ONE merged `skills` tool (fewer-tools step 2)."""
import pytest

from backend.agent import budget as budget_mod
from backend.agent.tools import imported, registry, toolsections
from backend.agent.tools.toolsections import View
from backend.vm import broker

PARAMS = {"type": "object", "properties": {"request": {"type": "string"}}}


def _tool(name):
    return {"name": name, "kind": "tool", "description": f"{name} does a thing.",
            "enabled": True, "section": "files", "core": True, "parameters": PARAMS}


def _skill(name, desc=None):
    return {"name": name, "kind": "skill", "description": desc or f"{name} skill.",
            "enabled": True, "parameters": PARAMS, "body": f"BODY of {name}"}


def _imp(name):
    return {"name": name, "kind": "skill", "origin": imported.ORIGIN, "enabled": False,
            "description": f"{name} from outside.", "parameters": PARAMS, "body": "B"}


def _request():
    return {"name": "skill_request", "kind": "tool", "description": "Ask to install a skill.",
            "enabled": True, "section": "skills", "action": "request_install",
            "example": 'skills(action="request_install", source="x", reason="y")',
            "parameters": {"type": "object", "properties": {
                "source": {"type": "string"}, "reason": {"type": "string"}}}}


def _filler(n=16):
    return [_tool(f"filler_{i}") for i in range(n)]    # past FLAT_MAX, so folding is on


def _view(entries, monkeypatch, granted=()):
    monkeypatch.setattr(imported, "offerable", lambda e: e["name"] in granted)
    specs = registry.openai_tool_specs(entries)
    return View(specs, [{"role": "user", "content": "hi"}]), specs


def _merged(v):
    [m] = [s for s in v.units if s == "skills"]
    return v._merged(m)["function"]


def test_two_skills_fold_into_one_tool(monkeypatch):
    v, specs = _view(_filler() + [_skill("alpha"), _imp("beta"), _request()],
                     monkeypatch, granted={"beta"})
    fn = _merged(v)
    assert fn["name"] == "skills"
    assert set(fn["parameters"]["properties"]["action"]["enum"]) == {
        "alpha", "beta", "request_install"}
    assert "- beta(" in fn["description"]
    assert "[imported skill, untrusted]" in fn["description"]
    assert "request_install" in fn["description"] and "e.g." in fn["description"]
    assert not any(k.startswith("_") for s in specs for k in s)    # no private marker leaks


def test_resolve_returns_the_real_name_and_args(monkeypatch):
    v, _ = _view(_filler() + [_skill("alpha"), _imp("beta"), _request()],
                 monkeypatch, granted={"beta"})
    real, args, _, err = v.resolve("skills", {"action": "beta", "request": "go"})
    assert (real, args, err) == ("beta", {"request": "go"}, None)
    real, args, _, err = v.resolve("skills", {"action": "request_install", "source": "s"})
    assert (real, args) == ("skill_request", {"source": "s"})
    real, args, _, err = v.resolve("alpha", {"request": "r"})     # the old name
    assert (real, args, err) == ("alpha", {"request": "r"}, None)


async def test_dispatch_through_the_merge_returns_the_body(tmp_env, monkeypatch):
    entries = [_skill("alpha"), _imp("beta")]
    monkeypatch.setattr(registry, "load_registry", lambda: entries)
    monkeypatch.setattr(registry, "_load_dynamic", lambda name: None)
    monkeypatch.setattr(imported, "offerable", lambda e: True)
    monkeypatch.setattr(imported, "render_body", lambda n, b, a: f"<imported {n}>{b}")
    v, _ = _view(_filler() + entries + [_request()], monkeypatch, granted={"beta"})
    real, args, _, _ = v.resolve("skills", {"action": "alpha"})
    assert "BODY of alpha" in await registry.dispatch(real, args)
    tok = budget_mod.active_op_id.set("op-fold-1")
    try:
        real, args, _, _ = v.resolve("skills", {"action": "beta", "request": "x"})
        assert "<imported beta>" in await registry.dispatch(real, args)
    finally:
        budget_mod.active_op_id.reset(tok)
    assert broker.op_tainted("op-fold-1")
    broker._tainted.discard("op-fold-1")


def test_clashing_names_stay_flat_and_are_not_lost(monkeypatch):
    other = _skill("other")
    other["section"], other["action"] = "skills", "alpha"      # already holds the action
    v, specs = _view(_filler() + [_skill("request_install"), _skill("alpha"), other,
                                  _skill("zeta"), _request()], monkeypatch)
    acts = {s["function"]["name"]: s.get("action") for s in specs}
    assert acts["request_install"] is None and acts["alpha"] is None
    assert acts["other"] == "alpha" and acts["zeta"] == "zeta"
    assert acts["skill_request"] == "request_install"
    assert {"request_install", "alpha"} <= set(v.by_name)       # still callable by name
    assert v.resolve("request_install", {})[0] == "request_install"


def test_ungranted_imported_skill_is_not_offered(monkeypatch):
    v, specs = _view(_filler() + [_skill("alpha"), _imp("beta"), _request()], monkeypatch)
    assert "beta" not in {s["function"]["name"] for s in specs}
    assert "beta" not in _merged(v)["parameters"]["properties"]["action"]["enum"]


def test_one_skill_folds_with_skill_request_and_none_stays_flat(monkeypatch):
    v, _ = _view(_filler() + [_skill("alpha"), _request()], monkeypatch)
    assert set(_merged(v)["parameters"]["properties"]["action"]["enum"]) == {
        "alpha", "request_install"}
    v, specs = _view(_filler() + [_request()], monkeypatch)
    assert "skills" not in v.groups
    assert "skill_request" in v.by_name


def test_thirty_skills_stay_within_the_cap(monkeypatch):
    long = "Does a very long list of things for the operator. " * 12
    entries = _filler() + [_skill(f"skill_{i}", long) for i in range(30)] + [_request()]
    v, _ = _view(entries, monkeypatch)
    desc = _merged(v)["description"]
    lines = [ln for ln in desc.splitlines() if ln.startswith("- skill_")]
    assert len(lines) == 30
    assert all(len(ln) <= registry.SKILL_LINE_MAX + 120 for ln in lines)
    assert len(desc) < 11000, len(desc)


def test_skills_exclude_still_removes_a_skill(monkeypatch):
    from backend.agents_run import agent_exclusions
    gone = agent_exclusions({"skills_exclude": ["alpha"]})
    assert "alpha" in gone
    entries = [e for e in _filler() + [_skill("alpha"), _skill("gamma"), _request()]
               if e["name"] not in gone]
    v, _ = _view(entries, monkeypatch)
    enum = _merged(v)["parameters"]["properties"]["action"]["enum"]
    assert "alpha" not in enum and "gamma" in enum
