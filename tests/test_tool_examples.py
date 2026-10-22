"""Every merged action carries one realistic example call, in the merged form
the model sees: section(action="<the file's action>", k=v, ...)."""
import ast
import json
import pathlib

import pytest
import yaml

from backend.agent.tools import registry, toolsections
from backend.agent.tools.toolsections import View

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = sorted(p for p in (ROOT / "tools").glob("*/TOOL.md")
               if "\naction:" in p.read_text())


def _front(path):
    return yaml.safe_load(path.read_text().split("---")[1])


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.parent.name)
def test_the_example_is_a_valid_merged_call(path):
    meta = _front(path)
    ex = meta.get("example")
    if path.parent.name == "skill_request" and not ex:
        pytest.skip("skill_request gets its example from the skills fold")
    assert isinstance(ex, str) and ex, f"{path.parent.name} has no example"
    assert len(ex) <= 160, len(ex)
    call = ast.parse(ex, mode="eval").body
    assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    assert call.func.id == meta["section"], (call.func.id, meta["section"])
    assert not call.args
    kw = {k.arg: k.value for k in call.keywords}
    assert isinstance(kw["action"], ast.Constant) and kw["action"].value == str(meta["action"])
    for v in kw.values():
        ast.literal_eval(v)                      # plain literals only
    params = meta.get("parameters") or {}
    props = params.get("properties") or {}
    own = "action" in props
    given = set()
    for k in kw:
        if k == "action":
            continue                             # the merged tool's action label
        name = "action" if (k == "do" and own) else k
        assert name in props, f"{k} is not a parameter of {path.parent.name}"
        given.add(name)
    for req in params.get("required") or []:
        assert req in given, f"{path.parent.name} example misses required {req}"


def test_the_merged_browser_description_carries_its_examples(monkeypatch):
    monkeypatch.setattr(registry, "_requirements_met",
                        lambda e: e.get("requires_local") is not True)
    specs = registry.openai_tool_specs(registry.load_registry())
    v = View(specs, [{"role": "user", "content": "open the browser tab"}])
    v.meta_call({"section": "browser"})
    tool = next(s for s in v.wire() if toolsections.spec_name(s) == "browser")
    desc = tool["function"]["description"]
    assert 'e.g. browser(action="click"' in desc
    assert 'e.g. browser(action="navigate"' in desc
    assert "example" not in json.dumps(tool["function"]["parameters"])
