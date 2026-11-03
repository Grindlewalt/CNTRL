"""nov3: search says when it is not working (chat #723, 2026-11-03).

SearXNG had one engine answering (Bing) and it returned pages that share a first
word with the query. search() handed them over as results and the research scout
read 48k tokens of junk. Now search says which engines did not answer, drops
results that do not match the query (and says how many), and research stops
instead of reading what is left. No test touches the network.
"""
import httpx

from backend import research, webtools
from backend.config import settings
from backend.db import init_db

DEAD = [["mojeek", "Suspended: access denied"], ["wikipedia", "no results"]]


def _hit(title, content="", url=None):
    return {"title": title, "content": content,
            "url": url or "https://x.test/" + title.replace(" ", "-")}


KYUJO_Q = ("Kyujo incident August 14 1945 Japanese officers attempted coup "
           "surrender recording Anami")
MADDOX_Q = ('Maddox "The Biggest Decision: Why We Had to Drop the Atomic Bomb" '
            "American Heritage 1995")
USSBS_Q = '"Japan\'s Struggle to End the War" United States Strategic Bombing Survey full text July 1946'

JUNK = {
    KYUJO_Q: [_hit("jQuery UI 1.14.2 released", "jQuery UI 1.14.2 is out with fixes."),
              _hit("jQuery UI 1.14.2 Changelog", "Changes in this release."),
              _hit("Menu - jQuery UI", "The menu widget themes a list of links.")],
    MADDOX_Q: [_hit("Maddox.games", "Games by Maddox."),
               _hit("Maddox - Padmount, Substation, and Dry-Type Transformers",
                    "Maddox makes transformers."),
               _hit("Maddox Ranch House", "A restaurant in Texas.")],
    USSBS_Q: [_hit("Japan - Wikipedia", "Japan is an island country in East Asia."),
              _hit("Travel Japan - Japan National Tourism Organization",
                   "Plan your trip to Japan.")],
}


def _fake_searx(monkeypatch, data):
    """Point search at a SearXNG that answers with `data`."""
    real = httpx.AsyncClient

    def handler(request):
        return httpx.Response(200, json=data)

    def client(*a, **kw):
        kw.pop("http2", None)
        return real(*a, transport=httpx.MockTransport(handler), **kw)
    monkeypatch.setattr(webtools.httpx, "AsyncClient", client)
    monkeypatch.setattr(settings, "searxng_engines", "bing,mojeek,wikipedia")


async def _seen(*a):
    return set()


def test_real_junk_does_not_match_and_good_results_do():
    for q, results in JUNK.items():
        keep, dropped = webtools._split_relevant(q, results)
        assert keep == [] and dropped == len(results), q
    good = [_hit("Kyushu Incident - Wikipedia",
                 "The Kyujo incident was an attempted coup on August 14 1945 by "
                 "Japanese officers to stop the surrender recording."),
            _hit("The attempted coup of August 1945",
                 "Officers tried to seize the surrender recording before Anami spoke.")]
    keep, dropped = webtools._split_relevant(KYUJO_Q, good)
    assert len(keep) == 2 and dropped == 0
    keep, _ = webtools._split_relevant("python asyncio gather exceptions", [
        _hit("asyncio.gather() and exceptions", "How gather handles exceptions."),
        _hit("Python docs: Coroutines and Tasks", "asyncio runs coroutines; gather them.")])
    assert len(keep) == 2
    keep, _ = webtools._split_relevant("sqlite", [_hit("SQLite Home Page")])
    assert len(keep) == 1                       # a one-word query needs one term


def test_health_line_format(monkeypatch):
    monkeypatch.setattr(settings, "searxng_engines", "bing,mojeek,wikipedia")
    assert webtools.search_health({"unresponsive_engines": []}) is None
    line = webtools.search_health({"unresponsive_engines": DEAD})
    assert line == ("(search is degraded: 1 of 3 engines answered; "
                    "mojeek: suspended: access denied; wikipedia: no results)")


async def test_search_flags_junk_and_names_the_dead_engines(tmp_env, monkeypatch):
    await init_db()
    monkeypatch.setattr(webtools, "fetched_set", _seen)
    _fake_searx(monkeypatch, {"results": JUNK[KYUJO_Q], "unresponsive_engines": DEAD})
    out = await webtools.search(KYUJO_Q, "s1")
    lines = out.splitlines()
    assert lines[2].startswith("(search is degraded: 1 of 3 engines answered")
    assert "do not match the query" in out and "Left out 3 of 3" in out
    assert "jQuery" not in out.split("do not match the query", 1)[1]


async def test_search_keeps_good_results_and_stays_quiet(tmp_env, monkeypatch):
    await init_db()
    monkeypatch.setattr(webtools, "fetched_set", _seen)
    good = [_hit("Kyujo incident - Wikipedia", "Attempted coup, August 14 1945, surrender recording.")]
    _fake_searx(monkeypatch, {"results": good, "unresponsive_engines": []})
    out = await webtools.search(KYUJO_Q, "s1")
    assert "degraded" not in out and "do not match" not in out and "Kyujo incident" in out


async def test_search_with_a_mixed_list_lists_only_the_matching(tmp_env, monkeypatch):
    await init_db()
    monkeypatch.setattr(webtools, "fetched_set", _seen)
    mixed = JUNK[KYUJO_Q] + [_hit("Kyujo incident", "Coup of August 14 1945 on the surrender recording.")]
    _fake_searx(monkeypatch, {"results": mixed, "unresponsive_engines": []})
    out = await webtools.search(KYUJO_Q, "s1")
    assert "Left out 3 of 4" in out and "the 1 that match follow" in out
    assert "1. Kyujo incident" in out and "jQuery" not in out.split("follow)", 1)[1]


async def test_search_results_drops_junk_and_records_notes(monkeypatch):
    _fake_searx(monkeypatch, {"results": JUNK[MADDOX_Q] + [
        _hit("The Biggest Decision, Maddox, American Heritage 1995",
             "Why we had to drop the atomic bomb.")], "unresponsive_engines": DEAD})
    notes = {}
    tok = webtools.search_notes.set(notes)
    try:
        out = await webtools.search_results(MADDOX_Q)
    finally:
        webtools.search_notes.reset(tok)
    assert [r["title"][:12] for r in out] == ["The Biggest "]
    assert notes["dropped"] == 3 and len(notes["health"]) == 1
    assert await webtools.search_results(MADDOX_Q)           # still a plain list with no notes


async def test_probe(monkeypatch):
    _fake_searx(monkeypatch, {"results": [_hit("Python (programming language)",
                                               "Python is a programming language.")],
                              "unresponsive_engines": []})
    r = await webtools.search_probe()
    assert r["ok"] and r["relevant"] == 1 and r["health"] is None


# --- research ---------------------------------------------------------------

async def test_filter_may_return_fewer_groups_or_none(monkeypatch):
    results = [{"url": "https://a.test/1", "title": "A", "snippet": ""}]

    async def empty(*a, **kw):
        return "[]"
    monkeypatch.setattr(research, "complete_text", empty)
    assert await research._filter_and_assign("t", results, 4) == []

    async def broken(*a, **kw):
        return "sorry, no json"
    monkeypatch.setattr(research, "complete_text", broken)
    assert len(await research._filter_and_assign("t", results, 4)) == 1   # old fallback


async def test_research_ends_without_readers_when_search_is_junk(tmp_env, monkeypatch):
    await init_db()
    (settings.projects_dir / "demo").mkdir(parents=True)
    health = "(search is degraded: 1 of 3 engines answered; mojeek: suspended: access denied)"

    async def queries(topic):
        return ["q one", "q two"]

    async def search(q, limit=6):
        n = webtools.search_notes.get()
        n["dropped"] = n.get("dropped", 0) + 5
        n.setdefault("health", set()).add(health)
        return [{"url": "https://a.test/1", "title": "A", "snippet": "a"}]

    async def never(*a, **kw):
        raise AssertionError("a reader ran")
    monkeypatch.setattr(research, "_gen_queries", queries)
    monkeypatch.setattr(webtools, "search_results", search)
    monkeypatch.setattr(webtools, "read", never)
    monkeypatch.setattr(research, "complete_text", never)   # not even the filter runs a reader

    async def filt(topic, results, n):
        return [{"theme": "A", "urls": ["https://a.test/1"]}]
    monkeypatch.setattr(research, "_filter_and_assign", filt)

    r = await research.run_research("Kyujo incident", "demo", job_id="job-junk")
    assert "nothing relevant" in r["doc"] and health in r["doc"]
    assert "try again later" in r["doc"] and r["doc_status"] == "canonical"
