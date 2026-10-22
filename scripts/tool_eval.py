#!/usr/bin/env python3
"""Tool-choice eval: does the model pick the right tool and action?

Builds the granted tool list from the REAL registry (every requirement treated
as met: "everything connected"), presents it through the real
toolsections.View, makes one model call (plus one more after a tools() hop)
and scores the call with View.resolve. Run it before and after a change to how
tools are presented and compare the two runs.

    python scripts/tool_eval.py --dry-run                     # tool counts, no model
    python scripts/tool_eval.py --out before.jsonl --repeat 3
    python scripts/tool_eval.py --compare before.jsonl after.jsonl

Model access is host-side and read-only: base URL and model id from the
provider registry (Settings overrides included), key from the env
JAV3_EVAL_API_KEY or, failing that, the provider's key as the gateway reads it
(secrets store, then JARVIS_DEEPSEEK_API_KEY). The key is never printed. The
script writes nothing under the state dir: the registry compile, skill grants
and security-event probe all go to a throw-away temp dir.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_CASES = ROOT / "tests" / "fixtures" / "tool_eval" / "cases.yaml"
OPENCLAW_FIXTURES = ROOT / "tests" / "fixtures" / "openclaw"
SYSTEM_PROMPT = (
    "You are Jarvis, an assistant that works for the operator on their projects. "
    "Use the tools you are given to do what the operator asks. Call the single "
    "tool that fits the request; do not answer from memory when a tool applies.")
# our own skills added to the fixture set (the imported ones come from OPENCLAW_FIXTURES)
OUR_SKILLS = {
    "release-notes": "Write release notes for a version from the project's git history and journal.",
    "standup-summary": "Summarize what changed in the project since yesterday as a short standup update.",
}
# room for DeepSeek's thinking before the tool call: at 64 tokens it ate the
# whole reply (grounding probe, 2026-09-27); production leaves thinking on
MAX_TOKENS = 4096


# --- cases -----------------------------------------------------------------

def load_cases(path: Path | str) -> list[dict]:
    import yaml
    data = yaml.safe_load(Path(path).read_text())
    cases = data["cases"] if isinstance(data, dict) else data
    seen = set()
    for c in cases:
        if not c.get("id") or not c.get("prompt") or not (c.get("expect") or {}).get("tool"):
            raise ValueError(f"case needs id, prompt and expect.tool: {c!r}")
        if c["id"] in seen:
            raise ValueError(f"duplicate case id {c['id']}")
        seen.add(c["id"])
    return cases


# --- the tool set -------------------------------------------------------------

class Toolset:
    """Granted specs per variant (local chat or not, skills or not), built from
    the real registry inside a temp state dir."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self._cache: dict[tuple[bool, bool], list[dict]] = {}
        self._entries: dict[bool, list[dict]] = {}

    def _compile(self, with_skills: bool) -> list[dict]:
        if with_skills in self._entries:
            return self._entries[with_skills]
        from backend.agent.tools import imported, registry
        from backend.config import settings
        base = self.tmp / ("skills-on" if with_skills else "skills-off")
        data, skills = base / "data", base / "skills"
        data.mkdir(parents=True)
        skills.mkdir()
        settings.data_dir, settings.skills_dir = data, skills
        settings.db_path = data / "eval-probe.db"
        if with_skills:
            for src in sorted(OPENCLAW_FIXTURES.iterdir()):
                if (src / "SKILL.md").is_file():
                    self._vendor(src, skills / f"{imported.IMPORT_PREFIX}{src.name}")
            for name, desc in OUR_SKILLS.items():
                d = skills / name
                d.mkdir()
                (d / "SKILL.md").write_text(
                    f"---\nname: {name}\ndescription: {desc}\n---\n\n{desc}\n")
        entries = registry.compile_registry()      # writes data/registry.json: temp
        self._entries[with_skills] = entries
        return entries

    @staticmethod
    def _vendor(src: Path, dest: Path) -> None:
        """Copy a fixture skill, pin it and grant it (what an import plus the
        operator's grant leave behind), all inside the temp dir."""
        from backend.agent.tools import imported
        shutil.copytree(src, dest)
        files = {p.relative_to(dest).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(dest.rglob("*")) if p.is_file()}
        (dest / imported.PIN_FILE).write_text(json.dumps(
            {"origin": imported.ORIGIN, "name": src.name, "source": "fixture",
             "ref": "", "imported_at": "", "files": files, "scan": []}))
        imported.set_grant(dest, True)

    def specs(self, local: bool = False, skills: bool = False) -> list[dict]:
        key = (bool(local), bool(skills))
        if key in self._cache:
            return self._cache[key]
        from backend import localexec
        from backend.agent.tools import imported, registry
        entries = [e for e in self._compile(bool(skills)) if not e.get("internal")]
        if local:
            keep = localexec.filter_entries(entries)
            entries = [e for e in keep if e["name"] not in localexec._SANDBOX_TOOLS]
        else:
            entries = [e for e in entries if not e.get("requires_local")]
        # everything connected: desk, browser, projector, local... all offered; an
        # imported skill is offered once granted (its requirements treated as met)
        met = registry._requirements_met, imported.offerable
        registry._requirements_met = lambda e: True
        imported.offerable = lambda e: (e.get("granted") is True and not e.get("blocked")
                                        and not e.get("clash"))
        try:
            out = registry.openai_tool_specs(entries)
        finally:
            registry._requirements_met, imported.offerable = met
        self._cache[key] = out
        return out


# --- one case --------------------------------------------------------------------

def present(case: dict, specs: list[dict]):
    """The real View for this case, with its preloads applied."""
    from backend.agent.tools.toolsections import View
    history = [{"role": "user", "content": case["prompt"]}]
    view = View(specs, history)
    for sec in case.get("preload") or []:
        view.meta_call({"section": sec})
    return view


def _msg_calls(reply: dict) -> list[dict]:
    return [{"id": c.get("id") or f"call_{i}", "name": c["name"], "args": c.get("args") or {}}
            for i, c in enumerate(reply.get("tool_calls") or [])]


def _assistant_msg(reply: dict, call: dict) -> dict:
    m = {"role": "assistant", "content": reply.get("content") or None,
         "tool_calls": [{"id": call["id"], "type": "function", "function": {
             "name": call["name"], "arguments": json.dumps(call["args"])}}]}
    if reply.get("reasoning"):
        m["reasoning_content"] = reply["reasoning"]
    return m


def run_case(case: dict, specs: list[dict], model) -> dict:
    """model(messages, tools) -> {tool_calls: [{id, name, args}], content,
    reasoning, usage: {prompt_tokens, completion_tokens}}."""
    expect = case["expect"]
    rec = {"id": case["id"], "expect": expect["tool"], "correct": False, "hops": 0,
           "first_call": None, "chosen": None, "wire_tools": 0, "prompt_tokens": 0,
           "completion_tokens": 0, "latency_s": 0.0, "error": None}
    t0 = time.time()
    try:
        view = present(case, specs)
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": case["prompt"]}]
        wire = view.wire()
        rec["wire_tools"] = len(wire)
        reply = model(messages, wire)
        _add_usage(rec, reply)
        calls = _msg_calls(reply)
        rec["first_call"] = _call_label(calls[0]) if calls else None
        if calls and view.is_meta(calls[0]["name"]):
            rec["hops"] = 1
            messages.append(_assistant_msg(reply, calls[0]))
            messages.append({"role": "tool", "tool_call_id": calls[0]["id"],
                             "content": view.meta_call(calls[0]["args"])})
            wire = view.wire()
            rec["wire_tools_after_hop"] = len(wire)
            reply = model(messages, wire)
            _add_usage(rec, reply)
            calls = _msg_calls(reply)
        call = next((c for c in calls if not view.is_meta(c["name"])), None)
        if call is not None:
            real, args, _note, err = view.resolve(call["name"], call["args"])
            rec["chosen"] = real if not err else f"{call['name']} (refused)"
            ok = err is None and real == expect["tool"]
            if ok and expect.get("action") is not None:
                ok = args.get("action") == expect["action"]
            rec["correct"] = bool(ok)
    except Exception as e:  # noqa: BLE001 - one failed case must not stop a run
        rec["error"] = f"{type(e).__name__}: {e}"[:300]
    rec["latency_s"] = round(time.time() - t0, 3)
    return rec


def _add_usage(rec: dict, reply: dict) -> None:
    u = reply.get("usage") or {}
    rec["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
    rec["completion_tokens"] += int(u.get("completion_tokens") or 0)


def _call_label(call: dict) -> str:
    a = call["args"] if isinstance(call["args"], dict) else {}
    sub = a.get("action") or a.get("section")
    return f"{call['name']}({sub})" if sub else call["name"]


# --- summaries ---------------------------------------------------------------------

def summarize(records: list[dict], price: dict | None = None) -> dict:
    n = len(records)
    if not n:
        return {"cases": 0}
    ok = [r for r in records if r["correct"]]
    pt = sum(r["prompt_tokens"] for r in records)
    ct = sum(r.get("completion_tokens", 0) for r in records)
    out = {
        "cases": n,
        "accuracy": len(ok) / n,
        "accuracy_no_hop": sum(1 for r in ok if r["hops"] == 0) / n,
        "mean_hops": sum(r["hops"] for r in records) / n,
        "mean_wire_tools": sum(r["wire_tools"] for r in records) / n,
        "mean_prompt_tokens": pt / n,
        "errors": sum(1 for r in records if r.get("error")),
        "mean_latency_s": sum(r["latency_s"] for r in records) / n,
    }
    if price:
        out["est_cost_usd"] = (pt * price["in"] + ct * price["out"]) / 1e6
    return out


def format_summary(s: dict, title: str = "") -> str:
    if not s.get("cases"):
        return f"{title}: no records"
    lines = [title] if title else []
    lines += [
        f"  cases                {s['cases']}",
        f"  accuracy             {s['accuracy']:.1%}",
        f"  accuracy, no hop     {s['accuracy_no_hop']:.1%}",
        f"  mean hops            {s['mean_hops']:.2f}",
        f"  mean tools on wire   {s['mean_wire_tools']:.1f}",
        f"  mean prompt tokens   {s['mean_prompt_tokens']:.0f}",
        f"  mean latency         {s['mean_latency_s']:.2f}s",
        f"  errors               {s['errors']}"]
    if "est_cost_usd" in s:
        lines.append(f"  est. cost            ${s['est_cost_usd']:.4f}")
    return "\n".join(lines)


def read_run(path: Path | str) -> list[dict]:
    return [json.loads(ln) for ln in Path(path).read_text().splitlines() if ln.strip()]


def _by_case(records: list[dict]) -> dict[str, dict]:
    """Per case over repeats: pass rate, hops, the calls made."""
    out: dict[str, dict] = {}
    for r in records:
        c = out.setdefault(r["id"], {"n": 0, "ok": 0, "hops": 0, "calls": []})
        c["n"] += 1
        c["ok"] += 1 if r["correct"] else 0
        c["hops"] += r["hops"]
        c["calls"].append(r.get("chosen") or r.get("first_call") or "-")
    return out


def compare(a: list[dict], b: list[dict]) -> str:
    ca, cb = _by_case(a), _by_case(b)
    lines = []
    for cid in sorted(set(ca) | set(cb)):
        x, y = ca.get(cid), cb.get(cid)
        if x is None or y is None:
            lines.append(f"{cid}: only in {'A' if y is None else 'B'}")
            continue
        ra, rb = x["ok"] / x["n"], y["ok"] / y["n"]
        ha, hb = x["hops"] / x["n"], y["hops"] / y["n"]
        if ra == rb and ha == hb and set(x["calls"]) == set(y["calls"]):
            continue
        tag = "FIXED" if rb > ra else "BROKE" if rb < ra else "changed"
        lines.append(f"{cid}: {tag}  correct {ra:.0%} -> {rb:.0%}  hops {ha:.1f} -> {hb:.1f}  "
                     f"chose {'/'.join(sorted(set(x['calls'])))} -> {'/'.join(sorted(set(y['calls'])))}")
    head = ["A:", format_summary(summarize(a)), "B:", format_summary(summarize(b)), ""]
    return "\n".join(head + (lines or ["no per-case differences"]))


# --- model access --------------------------------------------------------------------

def resolve_model(model_arg: str | None) -> dict:
    """base URL, model name, headers and price for the model, read-only. The
    provider registry (catalogue + Settings overrides) says where it lives;
    the key is JAV3_EVAL_API_KEY, else what the gateway would read."""
    from backend import providers
    mid = model_arg or providers.default_model()
    pid, name = providers.split_id(mid)
    pid = pid or providers.default_provider()
    p = providers.provider(pid)
    base = os.environ.get("JAV3_EVAL_BASE_URL") or providers._base(providers._load_state(), p)
    key = os.environ.get("JAV3_EVAL_API_KEY") or providers.api_key(pid)
    if not key and providers.needs_key(p):
        raise SystemExit(f"no API key for {pid}: set JAV3_EVAL_API_KEY")
    _known, price = providers.price_for(f"{pid}/{name}")
    return {"id": f"{pid}/{name}", "name": name, "base": base.rstrip("/"), "price": price,
            "headers": providers.auth_headers(p, key), "kind": p.get("kind") or "openai"}


def http_model(m: dict, timeout: float = 120.0, temperature: float | None = None):
    import httpx
    from backend.config import settings
    if m["kind"] not in ("openai", "ollama"):
        raise SystemExit(f"provider kind '{m['kind']}' is not OpenAI-compatible; use --model with one that is")
    client = httpx.Client(timeout=timeout)

    def call(messages: list[dict], tools: list[dict]) -> dict:
        payload = {"model": m["name"], "messages": messages, "tools": tools,
                   "tool_choice": "auto", "max_tokens": MAX_TOKENS,
                   # what a real turn sends (settings.model_temperature), unless
                   # --temperature pins it; thinking is left as production has it
                   "temperature": (settings.model_temperature if temperature is None
                                   else temperature),
                   "stream": False}
        last = None
        for attempt in range(3):
            r = client.post(m["base"] + "/chat/completions", json=payload,
                            headers={"Content-Type": "application/json", **m["headers"]})
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                last = r.status_code
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            break
        else:
            raise RuntimeError(f"HTTP {last}")
        data = r.json()
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        calls = []
        for c in msg.get("tool_calls") or []:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            calls.append({"id": c.get("id"), "name": fn.get("name", ""),
                          "args": args if isinstance(args, dict) else {}})
        return {"tool_calls": calls, "content": msg.get("content"),
                "reasoning": msg.get("reasoning_content"), "usage": data.get("usage") or {}}
    return call


# --- dry run ------------------------------------------------------------------------

def dry_run(cases: list[dict], toolset: Toolset, out=print) -> list[dict]:
    rows = []
    for c in cases:
        specs = toolset.specs(local=c.get("chat") == "local", skills=bool(c.get("skills")))
        view = present(c, specs)
        wire = view.wire()
        chars = len(SYSTEM_PROMPT) + len(c["prompt"]) + len(json.dumps(wire))
        rows.append({"id": c["id"], "granted": len(specs), "wire": len(wire),
                     "tokens": chars // 4,
                     "expect_granted": c["expect"]["tool"] in view.granted()})
    out(f"{'case':<28}{'granted':>8}{'on wire':>9}{'~tokens':>9}  expected tool granted")
    for r in rows:
        out(f"{r['id']:<28}{r['granted']:>8}{r['wire']:>9}{r['tokens']:>9}  "
            f"{'yes' if r['expect_granted'] else 'NO'}")
    n = len(rows) or 1
    out(f"{'mean':<28}{sum(r['granted'] for r in rows) / n:>8.1f}"
        f"{sum(r['wire'] for r in rows) / n:>9.1f}{sum(r['tokens'] for r in rows) / n:>9.0f}")
    return rows


# --- main ------------------------------------------------------------------------------

def main(argv: list[str] | None = None, model=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cases", default=str(DEFAULT_CASES))
    ap.add_argument("--out", help="write one JSON line per case run")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true", help="no model call; print tool counts")
    ap.add_argument("--compare", nargs=2, metavar=("A.jsonl", "B.jsonl"))
    ap.add_argument("--model", help="provider/model (default: the configured default)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only", help="comma-separated case ids")
    ap.add_argument("--temperature", type=float,
                    help="default: the configured model_temperature, as a real turn")
    args = ap.parse_args(argv)

    if args.compare:
        print(compare(read_run(args.compare[0]), read_run(args.compare[1])))
        return 0

    cases = load_cases(args.cases)
    if args.only:
        want = {s.strip() for s in args.only.split(",") if s.strip()}
        missing = want - {c["id"] for c in cases}
        if missing:
            print(f"unknown case ids: {', '.join(sorted(missing))}", file=sys.stderr)
            return 2
        cases = [c for c in cases if c["id"] in want]
    if args.limit:
        cases = cases[:args.limit]

    with tempfile.TemporaryDirectory(prefix="tool-eval-") as tmp:
        toolset = Toolset(Path(tmp))
        if args.dry_run:
            dry_run(cases, toolset)
            return 0
        price = None
        if model is None:
            m = resolve_model(args.model)
            model, price = http_model(m, temperature=args.temperature), m["price"]
            print(f"model {m['id']} at {m['base']}", file=sys.stderr)
        records = []
        out_f = open(args.out, "w") if args.out else None
        try:
            for rep in range(max(1, args.repeat)):
                for c in cases:
                    specs = toolset.specs(local=c.get("chat") == "local",
                                          skills=bool(c.get("skills")))
                    rec = run_case(c, specs, model)
                    rec["rep"] = rep
                    records.append(rec)
                    if out_f:
                        out_f.write(json.dumps(rec) + "\n")
                        out_f.flush()
                    mark = "ok " if rec["correct"] else "BAD"
                    print(f"{mark} {rec['id']:<28} {rec['first_call'] or '-'}"
                          + (f"  [{rec['error']}]" if rec["error"] else ""), file=sys.stderr)
        finally:
            if out_f:
                out_f.close()
        print(format_summary(summarize(records, price), "summary"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
