"""nov5: search asks again without the engines that answered junk, and research
spaces its searches out. On the Pi (2026-11-09) Bing answered the Kyujo-incident
query with YouTube help pages while Yahoo answered it with the Wikipedia article;
the scout's eight parallel searches were the burst that gets a home address
blocked. No test touches the network."""
import asyncio

import httpx

from backend import research, webtools
from backend.config import settings

Q = "Kyujo incident August 14 1945 Japanese officers attempted coup surrender recording Anami"
JUNK = [{"title": "YouTube", "content": "Enjoy the videos you love.", "url": "https://yt.test/",
         "engines": ["bing"]},
        {"title": "YouTube TV Help", "content": "Help with your account.",
         "url": "https://yt.test/help", "engines": ["bing"]}]
GOOD = [{"title": "Kyujo incident - Wikipedia",
         "content": "The Kyujo incident was an attempted military coup in Japan in August 1945 "
                    "by officers trying to stop the surrender recording.",
         "url": "https://en.wikipedia.org/wiki/Kyujo_incident", "engines": ["yahoo"]}]


def _searx(monkeypatch, answer):
    """A SearXNG whose answer depends on the engines asked: answer(engines) -> results."""
    asked = []
    real = httpx.AsyncClient

    def handler(request):
        engines = request.url.params.get("engines", "")
        asked.append(engines)
        return httpx.Response(200, json={"results": answer(engines)})

    def client(*a, **kw):
        kw.pop("http2", None)
        return real(*a, transport=httpx.MockTransport(handler), **kw)
    monkeypatch.setattr(webtools.httpx, "AsyncClient", client)
    monkeypatch.setattr(settings, "searxng_engines", "yahoo,bing,brave,mojeek,wikipedia")
    monkeypatch.setattr(webtools, "RETRY_PAUSE_S", 0)

    async def seen(*a):
        return set()
    monkeypatch.setattr(webtools, "fetched_set", seen)
    return asked


async def test_junk_from_one_engine_is_asked_again_without_it(monkeypatch):
    asked = _searx(monkeypatch, lambda e: JUNK if "bing" in e else GOOD)
    out = await webtools.search(Q, "s1")
    assert asked == ["yahoo,bing,brave,mojeek,wikipedia", "yahoo,brave,mojeek,wikipedia"]
    assert "Kyujo incident - Wikipedia" in out and "YouTube" not in out
    assert "unrelated pages from bing" in out and "do not match the query" not in out


async def test_a_good_answer_is_not_asked_twice(monkeypatch):
    asked = _searx(monkeypatch, lambda e: GOOD)
    out = await webtools.search(Q, "s1")
    assert len(asked) == 1 and "asking again" not in out


async def test_still_junk_after_the_retry_is_flagged_as_before(monkeypatch):
    asked = _searx(monkeypatch, lambda e: JUNK)
    out = await webtools.search(Q, "s1")
    assert len(asked) == 2 and "do not match the query" in out


async def test_research_gets_the_retried_answer_too(monkeypatch):
    _searx(monkeypatch, lambda e: JUNK if "bing" in e else GOOD)
    got = await webtools.search_results(Q)
    assert [r["url"] for r in got] == ["https://en.wikipedia.org/wiki/Kyujo_incident"]


async def test_research_runs_its_searches_a_couple_at_a_time(monkeypatch):
    live = peak = 0

    async def fake(q, limit=6):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.02)
        live -= 1
        return [{"url": f"https://x.test/{q}", "title": q, "snippet": ""}]
    monkeypatch.setattr(research.webtools, "search_results", fake)
    monkeypatch.setattr(research, "SEARCH_STAGGER_S", 0)
    out = await research._batch_search([f"q{i}" for i in range(8)])
    assert len(out) == 8 and peak == research.SEARCH_AT_ONCE == 2


def test_the_default_engines_include_yahoo():
    from backend.config import Settings
    assert "yahoo" in Settings.model_fields["searxng_engines"].default.split(",")
