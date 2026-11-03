"""Host-side web access core: SearXNG search + inert page fetch + a shared
fetch ledger so multiple agents don't scrape the same page (wasting tokens
and narrowing the diversity of what's gathered).

Everything here runs on the trusted host. When Jav3 moves into the VM, these
become the host proxy the VM calls — the VM never opens a raw socket to the
internet; it only ever receives the sanitised text these functions return.
"""
import asyncio
import datetime
import re
import time
from urllib.parse import urlparse

import httpx

from . import pdftext, webfallback
from .agent import imageresult
from .config import settings
from .db import get_db
from .websec import UnsafeURL, html_to_text, is_safe_url

# Browser-like headers: many sites 403 a bare/unknown agent or a request with
# no Accept headers. This fetcher only reads public pages and returns inert text.
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/125.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# set by tests to serve the fallback fetches from a fake
_fallback_transport = None

# --- short-TTL content cache ---------------------------------------------
# key -> (expires_at_monotonic, text). Shared helpers: summarize.py keeps its
# own dict of summaries keyed (url, focus) with the same TTL/size settings.
_page_cache: dict[str, tuple[float, str]] = {}


# the WHOLE extracted text of pages read lately (capped at web_full_max_chars),
# so #part=N and extract= reach past the first web_max_chars without a refetch.
# Entry: "head \x00 capped(0/1) \x00 text", head = the page's title/URL lines.
_full_cache: dict[str, tuple[float, str]] = {}
_PART = re.compile(r"#part=(\d+)$")


def _cache_get(cache: dict, key) -> str | None:
    now = time.monotonic()
    for k in [k for k, (exp, _) in cache.items() if exp <= now]:
        del cache[k]
    hit = cache.get(key)
    return hit[1] if hit else None


def _cache_put(cache: dict, key, text: str) -> None:
    cache[key] = (time.monotonic() + settings.web_cache_ttl_seconds, text)
    while len(cache) > settings.web_cache_max_entries:
        del cache[min(cache, key=lambda k: cache[k][0])]  # drop closest to expiry


def _same_host(a: str, b: str) -> bool:
    """Hostnames equal (case-insensitive) or differing only by a leading www."""
    ha = (urlparse(a).hostname or "").lower()
    hb = (urlparse(b).hostname or "").lower()
    return ha.removeprefix("www.") == hb.removeprefix("www.")


# --- search health and relevance ---------------------------------------------
# SearXNG answers 200 even when most engines refused it (CAPTCHA, suspended,
# rate-limited) and the one left may return pages that share only a first word
# with the query (chat #723). These helpers make search say so.
import contextvars
import re

_STOPWORDS = frozenset((
    "the and for with from that this these those what when where which who whom why how "
    "are was were has have had not but you your our their its into onto about over under "
    "than then them they also can could would should will may might did does done any "
    "all one two per via").split())
_WORD = re.compile(r"[^\W_]+", re.UNICODE)

# Research sets this to a dict before its batch of searches; search_results adds
# what it saw (health lines, how many results it dropped) so the scout can stop
# on junk. Left unset for every other caller.
search_notes: contextvars.ContextVar = contextvars.ContextVar("search_notes", default=None)


def _fold(word: str) -> str:
    """Crude suffix folding so 'bombs', 'bombing' and 'bombed' meet at 'bomb'."""
    for suf in ("ing", "ed", "es", "s"):
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            return word[:-len(suf)]
    return word


def _terms(text: str) -> set[str]:
    return {_fold(w) for w in _WORD.findall(text.lower())
            if len(w) >= 3 and w not in _STOPWORDS}


def _matches_query(qterms: set[str], title: str, snippet: str) -> bool:
    """True when title+snippet share enough distinctive query terms: 2 (3 for a
    long query), or 1 when the query has two terms or fewer."""
    if not qterms:
        return True
    need = 1 if len(qterms) <= 2 else (2 if len(qterms) <= 6 else 3)
    return len(qterms & _terms(f"{title} {snippet}")) >= need


def _split_relevant(query: str, results: list[dict]) -> tuple[list[dict], int]:
    """(results that match the query, how many did not). Each result is a
    SearXNG dict with title/content."""
    qterms = _terms(query)
    keep = [r for r in results
            if _matches_query(qterms, r.get("title") or "", r.get("content") or "")]
    return keep, len(results) - len(keep)


def search_health(data: dict) -> str | None:
    """The one degraded-search line, or None when every engine asked answered.
    Reads SearXNG's unresponsive_engines [[engine, reason], ...]."""
    bad = [(str(e[0]), str(e[1]) if len(e) > 1 else "no answer")
           for e in (data.get("unresponsive_engines") or []) if e]
    if not bad:
        return None
    asked = [e.strip() for e in settings.searxng_engines.split(",") if e.strip()]
    why = "; ".join(f"{n}: {r[:1].lower()}{r[1:]}" for n, r in bad)
    if asked:
        total = max(len(asked), len(bad))
        return f"(search is degraded: {total - len(bad)} of {total} engines answered; {why})"
    return f"(search is degraded: {len(bad)} engines did not answer; {why})"


async def search_probe() -> dict:
    """One canary query for the doctor: {ok, engines, answered, relevant, health, error}."""
    query = "python programming language"
    try:
        async with httpx.AsyncClient(timeout=settings.web_fetch_timeout) as c:
            r = await c.get(f"{settings.searxng_url}/search",
                            params=_search_params(query), headers=HEADERS)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    results = data.get("results") or []
    keep, _ = _split_relevant(query, results)
    return {"ok": True, "health": search_health(data), "results": len(results),
            "relevant": len(keep)}


def _search_params(query: str) -> dict:
    """SearXNG query params, pinning the working engines (config searxng_engines)
    so blocked defaults don't silently return zero results."""
    params = {"q": query, "format": "json"}
    if settings.searxng_engines.strip():
        params["engines"] = settings.searxng_engines.strip()
    return params


async def search_results(query: str, limit: int = 6) -> list[dict]:
    """Structured SearXNG results [{url, title, snippet}] — for the research
    pipeline's batch-search phase (as data, not a text blob)."""
    try:
        async with httpx.AsyncClient(timeout=settings.web_fetch_timeout,
                                     http2=True) as c:
            r = await c.get(f"{settings.searxng_url}/search",
                            params=_search_params(query), headers=HEADERS)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError):
        return []
    keep, dropped = _split_relevant(query, data.get("results") or [])
    notes = search_notes.get()
    if notes is not None:
        notes["dropped"] = notes.get("dropped", 0) + dropped
        health = search_health(data)
        if health:
            notes.setdefault("health", set()).add(health)
    out = []
    for res in keep[:limit]:
        url = res.get("url")
        if url:
            out.append({"url": url, "title": res.get("title", ""),
                        "snippet": (res.get("content") or "").strip()[:300]})
    return out


async def search(query: str, session: str) -> str:
    """Query SearXNG, return a compact text list of results. Results already
    pulled in this session are flagged so agents pick fresh sources."""
    try:
        async with httpx.AsyncClient(timeout=settings.web_fetch_timeout,
                                     http2=True) as c:
            r = await c.get(f"{settings.searxng_url}/search",
                            params=_search_params(query),
                            headers=HEADERS)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        return f"error: search backend unreachable: {e}"

    seen = await fetched_set(session)
    lines = [f"search: {query}",
             f"(today is {datetime.date.today():%Y-%m-%d} — include the current "
             "year in queries about recent events)"]
    health = search_health(data)
    if health:
        lines.append(health)
    for ans in (data.get("answers") or [])[:3]:
        text = ans if isinstance(ans, str) else ans.get("answer", "")
        if text:
            lines.append(f"[answer] {text}")
    for box in (data.get("infoboxes") or [])[:1]:
        if box.get("content"):
            lines.append(f"[infobox] {box['content'][:400]}")
    results = data.get("results") or []
    if results:
        keep, dropped = _split_relevant(query, results)
        if len(keep) * 2 < len(results):
            lines.append(
                "(these results do not match the query: the engines answering return "
                "unrelated pages. Do not use them as sources, tell the operator search "
                "is degraded, and read known URLs with web_read instead of retrying. "
                f"Left out {dropped} of {len(results)} results"
                + (f"; the {len(keep)} that match follow)" if keep else "; none match)"))
            results = keep
    elif not results:
        # a genuinely empty index for this term — re-running the SAME query
        # won't change that (the loop in the 07-21 Kevin-Rindal chat). Say so.
        has_info = bool(data.get("answers") or data.get("infoboxes"))
        lines.append(
            "(no web results for this exact query. Retrying the same terms will "
            "not help — either broaden/rephrase ONCE, or accept the person/topic "
            "has no indexed presence and report that."
            + (" An infobox above may still hold the answer." if has_info else ""))
    for i, res in enumerate(results[:settings.web_search_results], 1):
        url = res.get("url", "")
        flag = "  [already fetched — pick a different source]" if url in seen else ""
        lines.append(f"\n{i}. {res.get('title', '(no title)')}\n   {url}{flag}\n"
                     f"   {(res.get('content') or '').strip()[:280]}")
    lines.append("\nCite sources in your answer as markdown links using the URLs above.")
    return "\n".join(lines)


async def _turn_grants() -> tuple[str | None, set[str]]:
    """(project slug, secret names it may use) for the running turn — the
    project-scoped grant check web_read's secret substitution now makes, the
    same rule the proxy applies to wire injection (egress.granted_secrets).
    A project-less chat is judged by the Default profile's secrets."""
    from . import egress
    from .agent.tools.toolctx import active_slug
    slug = await active_slug()
    db = await get_db()
    try:
        return slug, await egress.granted_secrets(db, slug)
    finally:
        await db.close()


def full_text(url: str) -> str | None:
    """The whole cached text of a page read lately, or None. extract= and
    read_and_summarize search this instead of the first part only."""
    hit = _cache_get(_full_cache, _PART.sub("", url))
    return hit.split("\x00", 2)[2] if hit else None


def _slice(base: str, hit: str, part: int) -> str:
    head, capped, text = hit.split("\x00", 2)
    size = settings.web_max_chars
    if part < 1 or (part - 1) * size >= max(len(text), 1):
        n = max(1, -(-len(text) // size))
        return f"error: {base} has {n} part{'s' if n != 1 else ''}; there is no part {part}."
    body = text[(part - 1) * size:part * size]
    lead = head if part == 1 else f"{base} (part {part})\n\n"
    note = ""
    if len(text) > size:
        note = "\n\n" + webfallback.slice_note(base, part, size, len(text), capped == "1")
    return lead + body + note


async def read(url: str, session: str) -> str:
    """Fetch a page and return inert plain text. SSRF-guarded. Claims the URL
    in the shared ledger BEFORE fetching, so parallel bots never pull the same
    page; a failed fetch releases the claim so it can be retried.

    {{secret:NAME}} placeholders are substituted for the fetch ONLY — and only
    onto the secret's bound hosts (see secrets.substitute_url). The ledger,
    cache, and everything returned to the model keep the placeholder form;
    fetched bodies and error strings are scrubbed because APIs love echoing
    the key back in error payloads."""
    from . import secrets as secrets_mod
    m = _PART.search(url)
    if m:
        # a later slice of a page read lately; the fetched URL never carries
        # the fragment. Gone from the cache: read the page again, then slice.
        base, part = url[:m.start()], int(m.group(1))
        hit = _cache_get(_full_cache, base)
        if hit is None:
            await release(session, base)
            first = await read(base, session)
            hit = _cache_get(_full_cache, base)
            if hit is None:
                return first
        return _slice(base, hit, part)
    fetch_url = url
    if "{{secret:" in url:
        try:
            slug, granted = await _turn_grants()
            fetch_url = secrets_mod.substitute_url(url, granted=granted, project=slug)
        except (KeyError, ValueError) as e:
            return f"error: {str(e).strip(chr(39))}"
    substituted = fetch_url != url

    def _out(text: str) -> str:
        return secrets_mod.scrub(text) if substituted else text

    # the guard's getaddrinfo is synchronous DNS — run it in a worker thread so
    # a slow resolver can't stall every SSE stream on the shared event loop.
    # `is_safe_url` is resolved from module globals at call time on purpose
    # (tests monkeypatch webtools.is_safe_url).
    async def _check_safe(u: str) -> None:
        await asyncio.get_running_loop().run_in_executor(None, is_safe_url, u)

    try:
        await _check_safe(fetch_url)
    except UnsafeURL as e:
        return _out(f"error: refused to fetch — {e}")

    # still record the claim on a cache hit: the ledger keeps flagging the URL
    # as fetched in search results (source diversity), but the re-read is free
    claimed = await claim(session, url)
    cached = _cache_get(_page_cache, url)
    if cached is not None:
        return cached + "\n\n(served from cache)"
    # a PDF is read a slice at a time (#page=N-M, #search=word): a document
    # already parsed answers those without another download
    doc_url, frag, frag_value = pdftext.split_fragment(url)
    doc = pdftext.cached(doc_url) if frag else None
    if doc is not None:
        out = _out(await pdftext.answer(doc_url, doc, frag, frag_value,
                                        settings.web_pdf_max_chars))
        if imageresult.MARKER not in out:   # a result with an image is rebuilt, not cached
            _cache_put(_page_cache, url, out)
        return out
    fetch_url = pdftext.split_fragment(fetch_url)[0]
    if not claimed:
        return (f"note: {url} was already claimed this session (by you or "
                "another bot). Pick a different source to diversify — or say "
                "why you need it again.")

    ok = False
    try:
        async with httpx.AsyncClient(timeout=settings.web_fetch_timeout,
                                     http2=True) as c:
            # manual redirect loop: same-host 3xx are followed (SSRF-rechecked
            # each hop); a cross-host redirect is handed back to the model so
            # an open redirect can't be laundered into a fetch we never vetted
            current, raw, ctype = fetch_url, b"", ""
            for _ in range(10):
                async with c.stream("GET", current, headers=HEADERS) as r:
                    if 300 <= r.status_code < 400:
                        loc = r.headers.get("location")
                        if not loc:
                            return _out(f"error: {current} answered with redirect "
                                        f"status {r.status_code} but no Location header")
                        target = str(httpx.URL(current).join(loc))
                        if not _same_host(current, target):
                            return _out(f"error: not followed — {url} redirects to a "
                                        f"different host: {target}. If that destination "
                                        "is what you want, call the tool again with that URL.")
                        try:
                            await _check_safe(target)
                        except UnsafeURL as e:
                            return _out(f"error: refused after redirect — {e}")
                        current = target
                        continue
                    r.raise_for_status()
                    ctype = r.headers.get("content-type", "")
                    limit = (settings.web_max_pdf_bytes
                             if pdftext.looks_like_pdf(ctype, b"", current)
                             else settings.web_max_bytes)
                    chunks, total = [], 0
                    async for chunk in r.aiter_bytes():
                        chunks.append(chunk)
                        total += len(chunk)
                        if total > limit:
                            break
                    raw = b"".join(chunks)
                    break
            else:
                return _out(f"error: too many redirects (>10) fetching {url}")
        if pdftext.looks_like_pdf(ctype, raw[:5]):
            if total > limit:
                return _out(f"error: the PDF at {doc_url} is over {limit // 1_000_000} MB, "
                            "too big to read here.")
            try:
                doc = await pdftext.load(doc_url, raw)
            except pdftext.PdfError as e:
                return _out(f"error: {e}")
            out = _out(await pdftext.answer(doc_url, doc, frag, frag_value,
                                            settings.web_pdf_max_chars))
            ok = True
            if imageresult.MARKER not in out:
                _cache_put(_page_cache, url, out)
            return out
        if b"\x00" in raw[:4096] and "html" not in ctype and "text" not in ctype:
            # images, archives, office files: decoded as text they were pages
            # of junk in the model's context
            return _out(f"error: {url} is binary content ({ctype or 'unknown type'}), "
                        "not a page or a PDF, so there is no text to read.")
        body = raw.decode("utf-8", errors="replace")
        html = ""
        if "html" in ctype or "<html" in body[:2000].lower():
            html = body
            title, text = html_to_text(body)
        else:
            title, text = "", body  # plain text / markdown / json served as-is
        lead = ""
        if html and webfallback.is_thin(html, text):
            # JavaScript-built or menus only: look for the text elsewhere. A
            # URL that had a secret substituted gets the page-local rung only
            got, tried = await webfallback.find_text(
                fetch_url, html, _check_safe, _fallback_transport, local_only=substituted)
            ok = True
            if not got:
                out = _out(webfallback.nothing_note(
                    url, title, tried, webfallback.meta_description(html)))
                _cache_put(_page_cache, url, out)
                return out
            label, text = got
            lead = f"(read from: {label})\n"
        ok = True
        capped = len(text) > settings.web_full_max_chars
        text = text[:settings.web_full_max_chars] or "(no readable text extracted)"
        head = lead + (f"# {title}\n{url}\n\n" if title else f"{url}\n\n")
        entry = "\x00".join([_out(head), "1" if capped else "0", _out(text)])
        _cache_put(_full_cache, url, entry)
        out = _out(_slice(url, entry, 1))
        _cache_put(_page_cache, url, out)  # keyed by the URL as requested
        return out
    except httpx.HTTPError as e:
        msg = f"error: fetch failed: {e}"
        if substituted and isinstance(e, httpx.HTTPStatusError) \
                and e.response.status_code in (401, 403):
            # the scrubbed error looks identical to "never substituted", which
            # misleads the model into hunting for a substitution problem
            msg += ("\nnote: the {{secret:...}} placeholder WAS substituted with "
                    "the real value before this fetch — the ORIGIN rejected the "
                    "credential itself. Likely a wrong/expired/not-yet-activated "
                    "key (many APIs take minutes-to-hours to activate new keys). "
                    "Tell the operator to verify the key in its provider "
                    "dashboard; do not troubleshoot the placeholder mechanism.")
        return _out(msg)
    finally:
        if not ok:
            await release(session, url)  # let a failed/refused fetch be retried


# --- fetch ledger ------------------------------------------------------------

async def fetched_set(session: str) -> set[str]:
    db = await get_db()
    try:
        async with db.execute(
            "SELECT url FROM fetched_urls WHERE session = ?", (session,)) as cur:
            return {r["url"] for r in await cur.fetchall()}
    finally:
        await db.close()


async def claim(session: str, url: str) -> bool:
    """Reserve a URL before fetching. Returns True if this caller got the claim,
    False if another bot already claimed it (INSERT OR IGNORE + rowcount is the
    atomic test-and-set; WAL lets parallel bots race it safely)."""
    from . import runtime
    if runtime.ephemeral.get():
        return True  # incognito: no ledger, no dedup
    db = await get_db()
    try:
        cur = await db.execute(
            "INSERT OR IGNORE INTO fetched_urls (session, url) VALUES (?, ?)",
            (session, url))
        await db.commit()
        return cur.rowcount == 1
    finally:
        await db.close()


async def release(session: str, url: str) -> None:
    """Drop a claim so a failed/refused fetch can be retried by anyone."""
    from . import runtime
    if runtime.ephemeral.get():
        return
    db = await get_db()
    try:
        await db.execute(
            "DELETE FROM fetched_urls WHERE session = ? AND url = ?", (session, url))
        await db.commit()
    finally:
        await db.close()


