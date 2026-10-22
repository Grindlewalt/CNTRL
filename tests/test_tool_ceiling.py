"""The hard ceiling: one request never carries more than MAX_VISIBLE function
names; the least recently used section is put away (toolsections.py)."""
from backend.agent.tools import toolsections
from backend.agent.tools.toolsections import MAX_VISIBLE, META, View


def _spec(name, section=None, **kw):
    s = {"type": "function", "function": {"name": name, "description": "d",
         "parameters": {"type": "object", "properties": {}}}}
    if section:
        s["section"] = section
    s.update(kw)
    return s


def _build(monkeypatch, sizes, meta=None, core=10, history=None, marked=()):
    """core tools plus sections (name -> tool count), no merging."""
    secs = {n: dict((meta or {}).get(n, {}), about=n) for n in sizes}
    secs["base"] = {"about": "base"}
    monkeypatch.setattr(toolsections, "SECTIONS", secs)
    specs = [_spec(f"core_{i}", "base", core=True) for i in range(core)]
    for sec, n in sizes.items():
        for i in range(n):
            specs.append(_spec(f"{sec}_t{i}", sec, **({"load": True} if sec in marked else {})))
    return View(specs, history)


def _names(v):
    return [toolsections.spec_name(s) for s in v.wire()]


def test_never_more_than_ceiling(monkeypatch):
    v = _build(monkeypatch, {c: 2 for c in "abcdef"})
    assert v.active and len(v.wire()) == 11
    for c in "abcdef":
        v.meta_call({"section": c})
        assert len(v.wire()) <= MAX_VISIBLE
    assert len(v.wire()) == MAX_VISIBLE


def test_lru_evicted_and_noted_once(monkeypatch):
    v = _build(monkeypatch, {c: 2 for c in "abcd"})
    v.meta_call({"section": "a"})
    v.meta_call({"section": "b"})
    out = v.meta_call({"section": "c"})
    assert out.count("section 'a' was put away") == 1
    assert "a" not in v.loaded and {"b", "c"} <= v.loaded
    assert "put away" not in v.meta_call({"section": "c"})


def test_call_to_put_away_tool_reloads_and_evicts(monkeypatch):
    v = _build(monkeypatch, {c: 2 for c in "abc"})
    v.meta_call({"section": "a"})
    v.meta_call({"section": "b"})
    v.meta_call({"section": "c"})                   # a is out
    real, _, note, err = v.resolve("a_t0", {})
    assert (real, err) == ("a_t0", None)
    assert "section 'a' is now loaded" in note
    assert "section 'b' was put away" in note
    assert "a" in v.loaded and len(v.wire()) <= MAX_VISIBLE


def test_call_refreshes_recency(monkeypatch):
    v = _build(monkeypatch, {c: 2 for c in "abc"})
    v.meta_call({"section": "a"})
    v.meta_call({"section": "b"})
    v.resolve("a_t1", {})                           # a is now newer than b
    v.meta_call({"section": "c"})
    assert "b" not in v.loaded and {"a", "c"} <= v.loaded


def test_autoload_and_with_never_evicted(monkeypatch):
    v = _build(monkeypatch, {"p": 1, "q": 1, "x": 2, "y": 2, "z": 2},
               meta={"p": {"autoload": True, "with": ["q"]}})
    assert {"p", "q"} <= v.loaded
    for c in "xyz":
        v.meta_call({"section": c})
    assert {"p", "q"} <= v.loaded
    assert len(v.wire()) <= MAX_VISIBLE


def test_oversize_section_stays(monkeypatch):
    v = _build(monkeypatch, {"a": 2, "big": 7})
    v.meta_call({"section": "a"})
    v.meta_call({"section": "big"})
    assert "big" in v.loaded and "a" not in v.loaded
    assert len(v.wire()) == 18                       # overflow is kept, never hidden


def test_turn_start_priority(monkeypatch):
    sizes = {"auto": 1, "used": 1, "named": 1, "trig": 3}
    v = _build(monkeypatch, sizes, marked=("used",),
               history=[{"role": "user", "content": "call named_t0 about the thing"}],
               meta={"auto": {"autoload": True}, "trig": {"triggers": "thing"}})
    assert v.loaded == {"auto", "used", "named"}     # trig (3 tools) would make 17
    assert len(v.wire()) <= MAX_VISIBLE
    v = _build(monkeypatch, {"auto": 1, "trig": 3, "fill": 2},
               meta={"auto": {"autoload": True}, "trig": {"triggers": "thing"}},
               history=[{"role": "user", "content": "the thing"}])
    assert v.loaded == {"auto", "trig"}


def test_flat_and_inactive_unchanged(monkeypatch):
    monkeypatch.setattr(toolsections, "SECTIONS", {"s": {}})
    specs = [_spec(f"t{i}", "s") for i in range(14)]
    v = View(specs)
    assert v.flat and not v.active and _names(v) == [f"t{i}" for i in range(14)]
    v = _build(monkeypatch, {"a": 2}, core=3)       # 5 units: not active
    assert not v.active and len(v.wire()) == 5


def test_wire_order_stays_registry_order(monkeypatch):
    v = _build(monkeypatch, {c: 2 for c in "abcd"})
    for c in "dcba":
        v.meta_call({"section": c})
    order = [s["function"]["name"] for s in v.specs]
    names = [n for n in _names(v) if n != META]
    assert names == sorted(names, key=order.index)
