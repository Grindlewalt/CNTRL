"""web_read says when it cut a page, reads past the cut, and does not hand a
JavaScript page's menus over as the page. No network: a mock transport."""
import json

import httpx

from backend import webtools, summarize
from backend.db import init_db

PROSE = ("The committee met on the first of August and agreed that the delegation "
         "would present its findings in full to the assembly. ")
MENU = "<nav>x</nav><ul>" + "".join(f"<li><a href='/m{i}'>Menu {i}</a></li>" for i in range(60)) + "</ul>"


def _page(body: str, head: str = "") -> str:
    return f"<html><head><title>T</title>{head}</head><body>{body}</body></html>"


def _long_index(n: int = 120) -> str:
    paras = "".join(f"<p>Document {i}. {PROSE}</p>" for i in range(1, n))
    return _page("<h1>Index</h1>" + paras)


def _serve(monkeypatch, routes: dict):
    """routes: url -> (status, content-type, body); unknown urls 404."""
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        hit = routes.get(str(req.url))
        if hit is None:
            return httpx.Response(404, text="no")
        status, ctype, body = hit
        return httpx.Response(status, headers={"content-type": ctype}, text=body)

    real = httpx.AsyncClient

    def factory(*a, **kw):
        return real(transport=httpx.MockTransport(handler), timeout=kw.get("timeout"))
    monkeypatch.setattr(webtools.httpx, "AsyncClient", factory)
    monkeypatch.setattr(webtools, "is_safe_url", lambda u: u)
    return calls


async def test_long_page_is_cut_with_a_note_and_part_reads_on(tmp_env, monkeypatch):
    await init_db()
    url = "https://nsarchive.example/NSAEBB162/index.htm"
    calls = _serve(monkeypatch, {url: (200, "text/html", _long_index())})
    first = await webtools.read(url, "s1")
    assert "(cut at 6,000 of " in first and f"{url}#part=2" in first
    assert "Document 119" not in first
    second = await webtools.read(url + "#part=2", "s1")
    assert "(part 2 of " in second and "Document" in second
    last = await webtools.read(url + "#part=99", "s1")
    assert last.startswith("error:") and "no part 99" in last
    assert calls == [url]                       # downloaded once, no fragment sent
    assert "Document 119" in webtools.full_text(url)


async def test_part_after_cache_expiry_refetches_without_fragment(tmp_env, monkeypatch):
    await init_db()
    url = "https://nsarchive.example/two.htm"
    calls = _serve(monkeypatch, {url: (200, "text/html", _long_index())})
    out = await webtools.read(url + "#part=2", "s2")
    assert "(part 2 of " in out
    assert calls == [url]


async def test_extract_searches_the_whole_page(tmp_env, monkeypatch):
    from tools.web_read import handler
    await init_db()
    url = "https://nsarchive.example/extract.htm"
    body = _long_index(700).replace("Document 118.", "Document 118. The zebra clause is here.")
    _serve(monkeypatch, {url: (200, "text/html", body)})
    seen = []

    async def fake_complete(system, user, **kw):
        seen.append(user)
        return "found zebra clause" if "zebra clause" in user else "not here"
    monkeypatch.setattr(summarize, "complete_text", fake_complete)

    async def sess():
        return "s3"
    monkeypatch.setattr(handler, "web_session", sess)
    out = await handler.run(url, "zebra clause")
    assert "found zebra clause" in out
    assert 1 <= len(seen) <= 4
    assert "searched parts" in out


JSONLD = _page(MENU, '<script type="application/ld+json">' + json.dumps(
    {"@type": "Article", "articleBody": PROSE * 12}) + "</script>")


async def test_jsonld_body_is_used_and_labelled(tmp_env, monkeypatch):
    await init_db()
    url = "https://news.example/a1"
    _serve(monkeypatch, {url: (200, "text/html", JSONLD)})
    out = await webtools.read(url, "s4")
    assert out.startswith("(read from: the page's JSON-LD article text)")
    assert "committee met" in out and "Menu 3" not in out


async def test_amp_alternate(tmp_env, monkeypatch):
    await init_db()
    url = "https://news.example/a2"
    amp = "https://news.example/a2/amp"
    page = _page(MENU, f'<link rel="amphtml" href="{amp}">')
    _serve(monkeypatch, {url: (200, "text/html", page),
                         amp: (200, "text/html", _page("<p>" + PROSE * 10 + "</p>"))})
    out = await webtools.read(url, "s5")
    assert out.startswith(f"(read from: the page's AMP version, {amp})")
    assert "committee met" in out


WAYBACK = "https://archive.org/wayback/available?url=https%3A%2F%2Fnews.example%2Fa3"


async def test_wayback_hit_has_date(tmp_env, monkeypatch):
    await init_db()
    url = "https://news.example/a3"
    snap = "http://web.archive.org/web/20230501120000/https://news.example/a3"
    raw = "http://web.archive.org/web/20230501120000id_/https://news.example/a3"
    avail = json.dumps({"archived_snapshots": {"closest": {
        "available": True, "url": snap, "timestamp": "20230501120000"}}})
    _serve(monkeypatch, {url: (200, "text/html", _page(MENU)),
                         WAYBACK: (200, "application/json", avail),
                         raw: (200, "text/html", _page("<p>" + PROSE * 10 + "</p>"))})
    out = await webtools.read(url, "s6")
    assert out.startswith(f"(read from: the Wayback Machine copy of 2023-05-01, {snap})")
    assert "committee met" in out


async def test_nothing_works_gives_honest_note_not_menus(tmp_env, monkeypatch):
    await init_db()
    url = "https://library.example/radio-report"
    big = _page(MENU + "<img src='data:image/png;base64," + "A" * 200_000 + "'>",
                '<meta name="description" content="A radio report to the nation.">')
    calls = _serve(monkeypatch, {url: (200, "text/html", big),
                                 "https://archive.org/wayback/available?url="
                                 "https%3A%2F%2Flibrary.example%2Fradio-report":
                                 (200, "application/json", '{"archived_snapshots": {}}')})
    out = await webtools.read(url, "s7")
    assert out.startswith("note:") and "JavaScript" in out
    assert "Wayback Machine (no snapshot)" in out and "headless_browser" in out
    assert "A radio report to the nation." in out and "Menu 3" not in out
    assert any("archive.org" in c for c in calls)


async def test_secret_url_never_reaches_archive(tmp_env, monkeypatch):
    await init_db()
    url = "https://api.example/page?apiKey={{secret:K}}"
    calls = _serve(monkeypatch, {"https://api.example/page?apiKey=sekrit":
                                 (200, "text/html", _page(MENU))})
    from backend import secrets as secrets_mod
    monkeypatch.setattr(secrets_mod, "substitute_url",
                        lambda u, **kw: u.replace("{{secret:K}}", "sekrit"))
    monkeypatch.setattr(secrets_mod, "scrub", lambda t: t.replace("sekrit", "***"))

    async def grants():
        return None, {"K"}
    monkeypatch.setattr(webtools, "_turn_grants", grants)
    out = await webtools.read(url, "s8")
    assert out.startswith("note:") and "sekrit" not in out
    assert not any("archive.org" in c for c in calls)


async def test_credential_query_skips_wayback(tmp_env, monkeypatch):
    await init_db()
    url = "https://news.example/a9?token=abc"
    calls = _serve(monkeypatch, {url: (200, "text/html", _page(MENU))})
    out = await webtools.read(url, "s9")
    assert "looks like a credential" in out
    assert not any("archive.org" in c for c in calls)


async def test_short_real_page_is_left_alone(tmp_env, monkeypatch):
    await init_db()
    url = "https://news.example/short"
    _serve(monkeypatch, {url: (200, "text/html", _page("<p>" + PROSE * 3 + "</p>"))})
    out = await webtools.read(url, "s10")
    assert "read from" not in out and "cut at" not in out and "committee met" in out
