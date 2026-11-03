"""What web_read does when a page comes back thin (built by JavaScript, menus
only): look for the text elsewhere, host-side and inert, and say what was tried.

Ladder, stopping at the first rung that yields real text:
  a. text embedded in the page itself (JSON-LD, Next.js/Nuxt data, <noscript>)
  b. alternates the page links (AMP, RSS/Atom, a print version)
  c. the Wayback Machine's copy
Every fetch here goes through the caller's SSRF check, is untrusted content
like the page, and has a short timeout under one total budget. The caller never
runs the ladder for a URL that had a {{secret:...}} substituted.

Also here: slicing and chunking long pages so #part=N and extract= can reach
all of one."""
import asyncio
import datetime
import json
import re
from html import unescape
from urllib.parse import quote, urljoin, urlparse

import httpx

from .websec import UnsafeURL, html_to_text

TOTAL_BUDGET = 25.0      # seconds for the whole ladder
STEP_TIMEOUT = 8.0       # seconds for one fetch
MAX_BYTES = 2_000_000
MIN_REAL_CHARS = 500     # less than this is not "the page's text"

_SENTENCE = re.compile(r"[A-Z\"“][^.!?\n]{40,}[.!?][\"”]?(?:\s|$)")
# a query string that looks like a credential never goes to a third party
_CREDENTIAL = re.compile(r"(?i)(?:^|[?&;])[^=&]*(token|key|sig|auth|session|"
                         r"secret|pass|credential)[^=&]*=")


def sentence_count(text: str) -> int:
    return len(_SENTENCE.findall(text))


def is_thin(html: str, text: str) -> bool:
    """True when the extracted text is tiny next to the HTML it came from, or
    is menus: short link-like lines and no sentence-like paragraphs."""
    n = len(text)
    if n >= 6_000:
        return False
    sentences = sentence_count(text)
    if n < 2_500 and len(html) > 20 * max(n, 1) and sentences < 6:
        return True
    return n < 4_000 and sentences < 2


def _walk_strings(node, out: list, key: str = ""):
    if isinstance(node, dict):
        for k, v in node.items():
            _walk_strings(v, out, k)
    elif isinstance(node, list):
        for v in node:
            _walk_strings(v, out, key)
    elif isinstance(node, str):
        out.append((key, node))


def _clean(s: str) -> str:
    if "<" in s and ">" in s:
        s = html_to_text(s)[1]
    return re.sub(r"[ \t]+", " ", unescape(s)).strip()


def _embedded(html: str) -> tuple[str, str] | None:
    """Rung a: (label, text) from data the page ships with, or None."""
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>',
                         html, re.S | re.I):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        found: list = []
        _walk_strings(data, found)
        bodies = [_clean(v) for k, v in found
                  if k in ("articleBody", "text") and len(v) >= MIN_REAL_CHARS]
        if bodies:
            return "the page's JSON-LD article text", max(bodies, key=len)
    # Next.js / Nuxt data blobs: long strings with spaces are prose
    blobs = re.findall(r'<script[^>]+id="__(?:NEXT|NUXT)_DATA__"[^>]*>(.*?)</script>',
                       html, re.S | re.I)
    for blob in blobs:
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        found = []
        _walk_strings(data, found)
        seen, parts = set(), []
        for _, v in found:
            if len(v) >= 300 and v.count(" ") > 30 and v not in seen:
                seen.add(v)
                parts.append(_clean(v))
        text = "\n\n".join(parts)
        if len(text) >= MIN_REAL_CHARS:
            return "the page's embedded Next.js/Nuxt data", text
    parts = [html_to_text(m)[1] for m in
             re.findall(r"<noscript[^>]*>(.*?)</noscript>", html, re.S | re.I)]
    text = "\n\n".join(p for p in parts if p)
    if len(text) >= MIN_REAL_CHARS and sentence_count(text) >= 2:
        return "the page's <noscript> content", text
    return None


def meta_description(html: str) -> str:
    for m in re.finditer(r"<meta\b[^>]*>", html, re.I):
        tag = m.group(0)
        if re.search(r'(?:name|property)=["\'](?:og:description|description)["\']', tag, re.I):
            c = re.search(r'content=(["\'])(.*?)\1', tag, re.S | re.I)
            if c and c.group(2).strip():
                return unescape(c.group(2)).strip()
    return ""


def alternates(html: str, base: str) -> list[tuple[str, str]]:
    """Rung b candidates: (label, url) the page itself points at."""
    out: list[tuple[str, str]] = []
    for m in re.finditer(r"<link\b[^>]*>", html, re.I):
        tag = m.group(0)
        href = re.search(r'href=(["\'])(.*?)\1', tag, re.I)
        rel = re.search(r'rel=(["\'])(.*?)\1', tag, re.I)
        if not href or not rel:
            continue
        r, low = rel.group(2).lower(), tag.lower()
        if "amphtml" in r:
            out.append(("the page's AMP version", urljoin(base, unescape(href.group(2)))))
        elif "alternate" in r and ("application/rss+xml" in low or "application/atom+xml" in low):
            out.append(("the page's RSS/Atom feed", urljoin(base, unescape(href.group(2)))))
    for m in re.finditer(r'<a\b[^>]*href=(["\'])(.*?)\1[^>]*>(.*?)</a>', html, re.S | re.I):
        href, label = m.group(2), re.sub(r"<[^>]+>", "", m.group(3)).strip().lower()
        if "print" in (href.lower() + label) and not href.lower().startswith(("javascript:", "#")):
            out.append(("the page's print version", urljoin(base, unescape(href))))
    seen, uniq = set(), []
    for label, u in out:
        if u not in seen and u != base:
            seen.add(u)
            uniq.append((label, u))
    return uniq[:4]


async def _fetch(client: httpx.AsyncClient, url: str, check) -> tuple[str, str] | None:
    """(content-type, text) of an inert GET, every hop SSRF-checked; None on
    any failure. Redirects are followed (3 hops) because archives redirect."""
    current = url
    for _ in range(4):
        try:
            await check(current)
            async with client.stream("GET", current, timeout=STEP_TIMEOUT,
                                     headers={"User-Agent": "Mozilla/5.0 (compatible)"}) as r:
                if 300 <= r.status_code < 400 and r.headers.get("location"):
                    current = str(httpx.URL(current).join(r.headers["location"]))
                    continue
                if r.status_code >= 400:
                    return None
                raw, total = [], 0
                async for chunk in r.aiter_bytes():
                    raw.append(chunk)
                    total += len(chunk)
                    if total > MAX_BYTES:
                        break
                return (r.headers.get("content-type", ""),
                        b"".join(raw).decode("utf-8", "replace"))
        except (httpx.HTTPError, UnsafeURL, ValueError):
            return None
    return None


def _real(text: str) -> bool:
    return len(text) >= MIN_REAL_CHARS and sentence_count(text) >= 2


async def _ladder(url, html, check, transport, tried: list, local_only=False) -> tuple[str, str] | None:
    tried.append("text embedded in the page (JSON-LD, Next.js/Nuxt data, <noscript>)")
    got = _embedded(html)
    if got:
        return got
    if local_only:
        tried.append("(nothing fetched elsewhere: the URL carries a secret, which must never reach another site)")
        return None
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as c:
        alts = alternates(html, url)
        tried.append("alternates the page links ("
                     + (", ".join(l.replace("the page's ", "") for l, _ in alts) or "none found")
                     + ")")
        for label, u in alts:
            res = await _fetch(c, u, check)
            text = html_to_text(res[1])[1] if res else ""
            if _real(text):
                return f"{label}, {u}", text
        if _CREDENTIAL.search(urlparse(url).query):
            tried.append("the Wayback Machine (skipped: the URL's query looks like a credential)")
            return None
        tried.append("the Wayback Machine")
        res = await _fetch(c, "https://archive.org/wayback/available?url="
                           + quote(url, safe=""), check)
        try:
            snap = json.loads(res[1])["archived_snapshots"]["closest"] if res else None
        except (ValueError, KeyError, TypeError):
            snap = None
        if not snap or not snap.get("available") or not snap.get("url"):
            tried[-1] = "the Wayback Machine (no snapshot)"
            return None
        ts = str(snap.get("timestamp", ""))
        raw_url = re.sub(r"/web/(\d+)/", r"/web/\1id_/", snap["url"], count=1)
        copy = await _fetch(c, raw_url, check)
        if copy:
            text = html_to_text(copy[1])[1]
            if _real(text) and not is_thin(copy[1], text):
                try:
                    date = datetime.datetime.strptime(ts[:8], "%Y%m%d").strftime("%Y-%m-%d")
                except ValueError:
                    date = ts or "unknown date"
                return f"the Wayback Machine copy of {date}, {snap['url']}", text
        tried[-1] = "the Wayback Machine (a snapshot exists but it has no more text than the page)"
    return None


async def find_text(url: str, html: str, check, transport=None,
                    local_only: bool = False) -> tuple[tuple[str, str] | None, list[str]]:
    """((label, text) or None, what was tried). Never raises; bounded in time.
    `check` is the caller's async SSRF check (raises UnsafeURL)."""
    tried: list[str] = []
    try:
        got = await asyncio.wait_for(_ladder(url, html, check, transport, tried, local_only), TOTAL_BUDGET)
    except Exception:  # noqa: BLE001 - a fallback never breaks the read (timeouts included)
        tried.append("(stopped: out of time or failed)")
        got = None
    return got, tried


def nothing_note(url: str, title: str, tried: list[str], description: str) -> str:
    out = [f"note: web_read could not get the text of {url}. It is loaded by "
           "JavaScript, so a plain fetch sees only the site's menus, which are not "
           "the page and are not handed over as if they were.",
           "Tried: " + "; ".join(tried) + "."]
    if description:
        out.append("All the page itself offers is its short description, not the text: "
                   + description[:400])
    out.append("Next: search for another copy of the document by its title"
               + (f" ({title[:100]})" if title else "")
               + ", or use headless_browser in a box with web access (the host never "
               "renders pages).")
    return "\n".join(out)


# --- long pages: slices and chunks -------------------------------------------

def slice_note(url: str, part: int, size: int, total: int, capped: bool) -> str:
    """The closing note of part N (N=1 is the first part web_read returns)."""
    parts = max(1, -(-total // size))
    plus = "+" if capped else ""
    if part >= parts:
        return (f"(end: part {parts} of {parts}, {total:,}{plus} characters in all"
                + ("; the page runs longer than the most this keeps" if capped else "") + ")")
    end = part * size
    if part == 1:
        return (f"(cut at {end:,} of {total:,}{plus} characters: read on with {url}#part=2, "
                "or ask web_read with extract= to search the whole page)")
    return (f"(part {part} of {parts}, characters {end - size + 1:,}-{end:,} of "
            f"{total:,}{plus}: read on with {url}#part={part + 1})")


def chunks(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


def pick(parts: list[str], request: str, limit: int = 4) -> list[int]:
    """Indexes (in page order) of the chunks most likely to hold the answer:
    ranked by hits of the request's words; with no hits, spread over the page."""
    if len(parts) <= limit:
        return list(range(len(parts)))
    stop = {"the", "and", "for", "with", "that", "this", "from", "what", "about"}
    words = {w for w in re.findall(r"\w{3,}", request.lower()) if w not in stop}
    scores = [sum(p.lower().count(w) for w in words) for p in parts]
    ranked = sorted(range(len(parts)), key=lambda i: (-scores[i], i))
    chosen = [i for i in ranked[:limit] if scores[i] > 0]
    if not chosen:
        step = len(parts) / limit
        chosen = [int(i * step) for i in range(limit)]
    return sorted(set(chosen))


def searched_note(chosen: list[int], total: int) -> str:
    if len(chosen) >= total:
        return ""
    return ("(searched parts " + ", ".join(str(i + 1) for i in chosen)
            + f" of {total}, the ones matching the request best; the rest of the "
            "page was not searched)")
