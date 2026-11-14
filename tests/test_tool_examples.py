"""A merged action MAY carry one example call (`example:` in its TOOL.md), in
the merged form the model sees: section(action="<the file's action>", k=v, ...).

None ship today: measured 2026-10-22 on DeepSeek V4.1 Flash (scripts/tool_eval.py,
docs/tool-eval-2026-10-22.md) the model already got 98-100% of its arguments
right without them, and examples on every action cost ~4.5% more prompt tokens.
Add one only for a tool whose arguments the model gets wrong; this checks it."""
import ast
import json
import pathlib

import pytest
import yaml

from backend.agent.tools import toolsections
from backend.agent.tools.toolsections import View

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = sorted(p for p in (ROOT / "tools").glob("*/TOOL.md")
               if "\naction:" in p.read_text() and "\nexample:" in p.read_text())


def _front(path):
    return yaml.safe_load(path.read_text().split("---")[1])


@pytest.mark.parametrize("path", FILES or [None], ids=lambda p: p.parent.name if p else "none")
def test_the_example_is_a_valid_merged_call(path):
    if path is None:
        pytest.skip("no TOOL.md carries an example")
    meta = _front(path)
    ex = meta.get("example")
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


def test_an_example_rides_its_action_line_and_never_the_schema():
    def spec(name, action, example=None):
        sp = {"type": "function", "section": "browser", "action": action,
              "function": {"name": name, "description": f"{name} does it.",
                           "parameters": {"type": "object",
                                          "properties": {"element": {"type": "string"}}}}}
        if example:
            sp["example"] = example
        return sp
    filler = [{"type": "function", "section": "files", "core": True, "function": {
        "name": f"f{i}", "description": "x", "parameters": {"type": "object"}}}
        for i in range(16)]
    v = View(filler + [spec("browser_click", "click", 'browser(action="click", element="f0:12")'),
                       spec("browser_back", "back")], [])
    v.meta_call({"section": "browser"})
    tool = next(s for s in v.wire() if toolsections.spec_name(s) == "browser")
    desc = tool["function"]["description"]
    assert 'e.g. browser(action="click", element="f0:12")' in desc
    assert desc.count("e.g.") == 1
    assert "example" not in json.dumps(tool["function"]["parameters"])
