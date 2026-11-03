import asyncio

from backend import summarize, webfallback, webtools
from backend.agent import imageresult
from backend.agent.tools.toolctx import web_session
from backend.config import settings

EXTRACT_SYSTEM = (
    "Extract exactly what the request asks for from this page text. Return only "
    "the extracted content, quotes or facts, no preamble. If the page does not "
    "contain it, say so in one line.")

MAX_CHUNKS = 4   # model calls per extract; the best-matching parts of a long page


async def run(url: str, extract: str = "") -> str:
    text = await webtools.read(url, await web_session())
    if text.startswith(("error:", "note:")):
        return text
    if isinstance(extract, str) and extract.strip():
        # a scanned PDF page carries its image after a marker: keep that out of
        # the extraction prompt and put it back, untouched, last
        text, img = imageresult.split(text)
        if img is not None:
            return _reattach(await _extract(url, extract, text, text), img)
        return await _extract(url, extract, text, webtools.full_text(url) or text)
    return text


def _reattach(text: str, img) -> str:
    if img.path and not img.b64:
        return imageresult.with_image(text, img.path, caption=img.caption)
    return imageresult.with_inline(text, b64=img.b64, mime=img.mime, caption=img.caption)


async def _extract(url: str, extract: str, text: str, full: str) -> str:
    # search the whole page, not the first part read() returned
    parts = webfallback.chunks(full, settings.web_max_chars * 2)
    chosen = webfallback.pick(parts, extract, MAX_CHUNKS)
    try:
        answers = await asyncio.gather(*(summarize.complete_text(
            EXTRACT_SYSTEM, f"Request: {extract}\n\nURL: {url}\n\n{parts[i]}")
            for i in chosen))
    except Exception:  # noqa: BLE001 — extraction is best-effort, raw text still answers
        return text
    if len(chosen) == 1:
        body = answers[0]
    else:
        body = "\n\n".join(f"(part {i + 1} of {len(parts)})\n{a}"
                           for i, a in zip(chosen, answers))
    note = webfallback.searched_note(chosen, len(parts))
    return f"{url}\n\n{body}" + (f"\n\n{note}" if note else "")
