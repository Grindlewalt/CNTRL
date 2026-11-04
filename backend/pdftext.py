"""PDF text for web_read (and everything else on webtools.read).

web_read used to decode a PDF's bytes as UTF-8 and hand the model junk. 2026-10-02:
an agent asked to look up a game's rules could not have read the rule book even
with the right URL (a 5 MB, ~150-page PDF).

A manual is far longer than one tool result, so a PDF is read a slice at a time
with the standard PDF URL fragments, which need no new tool parameter:

  <url>              page count, the outline (bookmarks -> pages), first pages
  <url>#page=12      one page;  #page=12-15  a range
  <url>#search=climb the pages that mention a term, with a snippet each

The parsed pages are cached per document URL (fragment dropped), so paging
through a manual downloads it once. Parsing runs in a worker thread.

A scanned page has no text layer. 2026-11-03: a 15-page government PDF, every
page a scan, came back as fifteen "(no text on this page)" lines and the model
gave up on a primary source. The scan is an image inside the PDF, so
`#page=K` on a scan page hands that image to a vision-capable chat model
(answer() below); the first read says how many pages are scans.
"""
import asyncio
import io
import re
import time
from urllib.parse import unquote

MAX_PAGES = 600            # pages parsed per document (a 150-page manual is common)
MAX_OUTLINE = 60           # outline entries shown on the first read
MAX_HITS = 12              # pages listed for a #search
SNIPPET = 160              # characters either side of a search hit
CACHE_TTL = 1800
CACHE_MAX = 8
RAW_MAX = 20_000_000       # a document with scan pages keeps its bytes up to this size...
RAW_TOTAL = 60_000_000     # ...and the cache holds at most this many such bytes in all
IMG_SIDE = 1600            # a scan is shown at most this many pixels on its long side
IMG_BYTES = 1_500_000      # and at most this many bytes
IMG_KEPT = 3               # converted page images kept per document

_FRAG = re.compile(r"#(page|search)=([^&#]*)", re.I)
_docs: dict[str, tuple[float, dict]] = {}     # document url -> (expires, parsed)


class PdfError(Exception):
    pass


def looks_like_pdf(ctype: str, head: bytes, url: str = "") -> bool:
    return ("pdf" in (ctype or "").lower() or head[:5] == b"%PDF-"
            or url.split("#", 1)[0].split("?", 1)[0].lower().endswith(".pdf"))


def split_fragment(url: str) -> tuple[str, str | None, str | None]:
    """(document url, 'page'|'search'|None, value) — only our two fragments
    are taken off; any other fragment stays part of the URL as before."""
    m = _FRAG.search(url)
    if not m:
        return url, None, None
    return url[:m.start()], m.group(1).lower(), unquote(m.group(2)).strip()


def cached(doc_url: str) -> dict | None:
    now = time.monotonic()
    for k in [k for k, (exp, _) in _docs.items() if exp <= now]:
        del _docs[k]
    hit = _docs.get(doc_url)
    return hit[1] if hit else None


def _remember(doc_url: str, doc: dict) -> None:
    _docs[doc_url] = (time.monotonic() + CACHE_TTL, doc)
    while len(_docs) > CACHE_MAX:
        del _docs[min(_docs, key=lambda k: _docs[k][0])]
    # the bytes of scanned documents are the heavy part: drop the oldest first
    held = sorted((k for k, (_, d) in _docs.items() if d.get("raw")),
                  key=lambda k: _docs[k][0])
    while held and sum(len(_docs[k][1]["raw"]) for k in held) > RAW_TOTAL:
        _docs[held.pop(0)][1]["raw"] = None


def _clean(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _has_image(page) -> bool:
    """True when the page draws an image (looked up in its resources, nothing
    decoded). Images inside a form XObject count, one level deep."""
    def look(res, depth):
        xo = (res or {}).get("/XObject")
        for ref in (xo.get_object().values() if xo is not None else ()):
            o = ref.get_object()
            sub = o.get("/Subtype")
            if sub == "/Image":
                return True
            if sub == "/Form" and depth < 1 and look(o.get("/Resources"), depth + 1):
                return True
        return False
    try:
        res = page.get("/Resources")
        return look(res.get_object() if res is not None else None, 0)
    except Exception:  # noqa: BLE001
        return False


def _parse(raw: bytes) -> dict:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise PdfError("this server cannot read PDFs (the pypdf package is missing; "
                       "the operator can reinstall requirements.txt)") from e
    try:
        reader = PdfReader(io.BytesIO(raw), strict=False)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as e:  # noqa: BLE001
                raise PdfError("the PDF is password-protected") from e
        n = len(reader.pages)
        pages, scans = [], []
        for i in range(min(n, MAX_PAGES)):
            try:
                pages.append(_clean(reader.pages[i].extract_text() or ""))
            except Exception:  # noqa: BLE001 — one bad page must not lose the rest
                pages.append("")
            if not pages[-1] and _has_image(reader.pages[i]):
                scans.append(i + 1)
        outline = []

        def walk(items, depth):
            for it in items:
                if len(outline) >= MAX_OUTLINE * 4:
                    return
                if isinstance(it, list):
                    walk(it, depth + 1)
                    continue
                try:
                    outline.append((depth, str(it.title).strip(),
                                    reader.get_destination_page_number(it) + 1))
                except Exception:  # noqa: BLE001
                    continue
        try:
            walk(reader.outline, 0)
        except Exception:  # noqa: BLE001 — a broken outline still leaves the text
            pass
        title = ""
        try:
            title = str((reader.metadata or {}).get("/Title") or "").strip()
        except Exception:  # noqa: BLE001
            pass
    except PdfError:
        raise
    except Exception as e:  # noqa: BLE001
        raise PdfError(f"could not parse the PDF ({type(e).__name__})") from e
    return {"pages": pages, "total": n, "outline": outline, "title": title,
            "scans": scans, "imgs": {},
            "raw": raw if scans and len(raw) <= RAW_MAX else None}


def _shrink(im) -> tuple[bytes, str] | None:
    """A PIL image -> (bytes, mime) within IMG_SIDE and IMG_BYTES: PNG when it
    fits (text scans compress well), else JPEG at falling quality, else smaller."""
    from PIL import Image
    im = im.convert("L" if im.mode in ("1", "L") else "RGB")
    side = IMG_SIDE
    while side >= 400:
        small = im.copy()
        small.thumbnail((side, side), Image.LANCZOS)
        buf = io.BytesIO()
        small.save(buf, "PNG", optimize=True)
        if buf.tell() <= IMG_BYTES:
            return buf.getvalue(), "image/png"
        rgb = small.convert("RGB")
        for q in (80, 60):
            buf = io.BytesIO()
            rgb.save(buf, "JPEG", quality=q)
            if buf.tell() <= IMG_BYTES:
                return buf.getvalue(), "image/jpeg"
        side = side * 2 // 3
    return None


def _page_image(raw: bytes, page_no: int) -> tuple[bytes, str] | str:
    """(bytes, mime) of the page's largest embedded image, or the reason it has none."""
    try:
        from PIL import Image
    except ImportError:
        return "this server has no image library (Pillow) to convert the scan"
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw), strict=False)
        if reader.is_encrypted:
            reader.decrypt("")
        found = list(reader.pages[page_no - 1].images)
    except Exception as e:  # noqa: BLE001
        return f"the page's images could not be read ({type(e).__name__})"
    best, best_px = None, 0
    for f in found:
        try:
            im = getattr(f, "image", None) or Image.open(io.BytesIO(f.data))
            im.load()
        except Exception:  # noqa: BLE001 — a format Pillow cannot decode (JBIG2...)
            continue
        if im.width * im.height > best_px:
            best, best_px = im, im.width * im.height
    if best is None:
        return ("the scan is in an image format this server cannot decode"
                if found else "the page has no image")
    try:
        out = _shrink(best)
    except Exception as e:  # noqa: BLE001
        return f"the scan could not be converted ({type(e).__name__})"
    return out or "the scan is too large to send even when shrunk"


async def page_image(doc: dict, page_no: int) -> tuple[bytes, str] | str:
    """The page's scan as (bytes, mime) ready for the model, or the reason it
    cannot be shown. Converted images are kept on the document, a few at a time."""
    hit = doc["imgs"].get(page_no)
    if hit:
        return hit
    if not doc.get("raw"):
        return "the PDF is no longer held here; read its URL again"
    got = await asyncio.to_thread(_page_image, doc["raw"], page_no)
    if not isinstance(got, str):
        doc["imgs"][page_no] = got
        while len(doc["imgs"]) > IMG_KEPT:
            del doc["imgs"][next(iter(doc["imgs"]))]
    return got


async def load(doc_url: str, raw: bytes) -> dict:
    doc = await asyncio.to_thread(_parse, raw)
    _remember(doc_url, doc)
    return doc


def _pages_block(doc: dict, first: int, last: int, budget: int) -> tuple[str, int]:
    """Pages first..last (1-based) inside `budget` chars -> (text, last page shown)."""
    out, used, shown = [], 0, first - 1
    for i in range(first, last + 1):
        body = doc["pages"][i - 1] if i <= len(doc["pages"]) else ""
        blank = ("(a scan: no text layer, the page is an image)" if i in doc.get("scans", ())
                 else "(no text on this page: a scan or a figure)")
        chunk = f"--- page {i} ---\n{body or blank}\n"
        if used + len(chunk) > budget and shown >= first:
            break
        if used + len(chunk) > budget:              # one page bigger than the budget
            chunk = chunk[:budget - used] + "\n[page cut at the size limit]\n"
        out.append(chunk)
        used += len(chunk)
        shown = i
    return "".join(out), shown


def render(doc_url: str, doc: dict, kind: str | None, value: str | None,
           budget: int) -> str:
    return _render(doc_url, doc, kind, value, budget)[0]


def _render(doc_url: str, doc: dict, kind: str | None, value: str | None,
            budget: int) -> tuple[str, int | None]:
    """(text, last page shown for a #page= read, else None)."""
    total, parsed = doc["total"], len(doc["pages"])
    head = f"{doc_url}\nPDF, {total} pages" + (f": {doc['title']}" if doc["title"] else "")
    if parsed < total:
        head += f" (text read for the first {parsed})"
    how = (f"\nRead more with the same tool: {doc_url}#page=N or #page=N-M for pages, "
           f"{doc_url}#search=WORD for the pages that mention a word.")
    if kind == "search":
        term = (value or "").lower()
        if not term:
            return head + "\nerror: #search= needs a word." + how, None
        hits = []
        for i, body in enumerate(doc["pages"], 1):
            low = body.lower()
            at = low.find(term)
            if at < 0:
                continue
            count = low.count(term)
            snip = body[max(0, at - SNIPPET):at + len(term) + SNIPPET].replace("\n", " ")
            hits.append(f"page {i} ({count}x): …{snip}…")
        if not hits:
            return head + f"\nno page mentions {value!r}." + how, None
        more = f"\n(+{len(hits) - MAX_HITS} more pages)" if len(hits) > MAX_HITS else ""
        return (head + f"\n{len(hits)} pages mention {value!r}:\n" + "\n".join(hits[:MAX_HITS])
                + more + how)[:budget + 2000], None
    if kind == "page":
        m = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", value or "")
        if not m:
            return head + f"\nerror: #page= wants N or N-M, got {value!r}." + how, None
        first = max(1, int(m.group(1)))
        last = min(int(m.group(2) or first), parsed)
        if first > parsed:
            return head + f"\nerror: page {first} is past the end ({parsed} pages read)." + how, None
        text, shown = _pages_block(doc, first, max(first, last), budget)
        tail = (f"\n[stopped at page {shown} to stay under the size limit: continue at "
                f"{doc_url}#page={shown + 1}-{last}]" if shown < last else "")
        return f"{head}\n\n{text}{tail}{how}", shown
    # the first read: outline, then as many opening pages as fit
    lines = []
    if doc["outline"]:
        lines.append("Outline (title → page):")
        for depth, title, page in doc["outline"][:MAX_OUTLINE]:
            lines.append(f"{'  ' * min(depth, 3)}- {title[:90]} → {page}")
        if len(doc["outline"]) > MAX_OUTLINE:
            lines.append(f"  … {len(doc['outline']) - MAX_OUTLINE} more entries")
    outline = "\n".join(lines)
    room = max(1500, budget - len(outline))
    text, shown = _pages_block(doc, 1, parsed, room)
    tail = f"\n[pages 1-{shown} of {total} shown]" if shown < total else ""
    scans = doc.get("scans") or []
    if scans:
        tail += (f"\n{len(scans)} of {total} pages are scans (no text layer): a scan "
                 f"page's image comes with web_read url={doc_url}#page=N, one page per call.")
    return f"{head}{how}\n\n{outline}\n\n{text}{tail}".replace("\n\n\n", "\n\n"), None


def _can_see() -> bool | None:
    """Whether the chat model takes images (None when that cannot be told)."""
    try:
        from . import providers
        pid, mid = providers.split_id(providers.default_model())
        info = providers.model_info(pid or providers.default_provider(), mid)
        return None if info is None else bool(info.get("vision"))
    except Exception:  # noqa: BLE001
        return None


async def answer(doc_url: str, doc: dict, kind: str | None, value: str | None,
                 budget: int) -> str:
    """render(), with a scan page's image attached (agent.imageresult carries it
    beside the text): on a #page= read the first scan page in it, and on the first
    read of a document with scans its first scan page, so a model that drops the
    #page= from the URL still sees one (chat #730 on the Pi asked for 33.pdf#page=1
    and the model called 33.pdf)."""
    text, shown = _render(doc_url, doc, kind, value, budget)
    if kind == "page" and shown is not None:
        m = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", value or "")
        first = max(1, int(m.group(1)))
        scans = [p for p in doc.get("scans", ()) if first <= p <= shown]
        rest = scans[1:]
        more = ("\nMore scan pages in this range, one image per read: "
                + ", ".join(f"{doc_url}#page={p}" for p in rest[:12]) + ".") if rest else ""
    elif kind is None:
        scans = list(doc.get("scans") or ())
        more = (f"\nFor another scan page, call web_read with url={doc_url}#page=N."
                if len(scans) > 1 else "")
    else:
        return text
    if not scans:
        return text
    if _can_see() is False:
        return (text + f"\nPage {scans[0]} is a scan, but the chat model cannot read images: "
                "switch to a model that can see images, or look for a text copy of "
                "this document." + more)
    got = await page_image(doc, scans[0])
    if isinstance(got, str):
        return text + f"\nPage {scans[0]} is a scan and its image could not be attached: {got}." + more
    from .agent import imageresult
    data, mime = got
    return imageresult.with_inline(
        text + f"\nPage {scans[0]} is a scan: its image is attached to this result." + more,
        data,
        mime=mime, caption=f"page {scans[0]} of {doc_url} (scan)")
