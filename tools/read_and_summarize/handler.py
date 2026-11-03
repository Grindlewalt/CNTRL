import asyncio

from backend.agent import imageresult
from backend import summarize, webfallback, webtools
from backend.agent.tools.toolctx import web_session
from backend.config import settings

MAX_URLS = 8


async def run(urls=None, url=None, focus: str = "", triage: bool = False) -> str:
    session = await web_session()
    targets = []
    if isinstance(urls, list):
        targets += [u for u in urls if isinstance(u, str) and u.strip()]
    if isinstance(urls, str) and urls.strip():
        targets.append(urls)
    if isinstance(url, str) and url.strip():
        targets.append(url)
    # de-dupe, preserve order, cap
    seen, ordered = set(), []
    for u in targets:
        if u not in seen:
            seen.add(u)
            ordered.append(u)
    ordered = ordered[:MAX_URLS]
    if not ordered:
        return "error: provide a url or a list of urls"

    async def one(u: str) -> str:
        try:
            text = await webtools.read(u, session)
        except Exception as e:  # noqa: BLE001 - surface, don't crash the run
            return f"Source: {u}\n(could not read: {e})"
        had_image = False
        if "\x00" in text:        # a scanned PDF page's image: summaries are text only
            text, img = imageresult.split(text)
            had_image = img is not None
        if text.startswith("note:"):
            return f"Source: {u}\n({text})"  # the page could not be read: say so whole
        if text.startswith("error:") or not text.strip():
            return f"Source: {u}\n({text[:150] or 'empty page'})"
        if triage:
            try:
                skip = await summarize.triage_page(text, u, focus)
            except Exception:  # noqa: BLE001 - triage is best-effort, keep the page
                skip = ""
            if skip:
                return f"Source: {u}\n(skipped by triage: {skip[:150]})"
        try:
            full = webtools.full_text(u)
            if full is None or len(full) <= settings.web_max_chars:
                s = await summarize.summarize_page(text, u, focus)
            else:
                # summarize the parts most likely to matter (up to 4), not just the start
                parts = webfallback.chunks(full, settings.web_max_chars)
                chosen = webfallback.pick(parts, focus, 4)
                if not focus.strip():
                    chosen = sorted({int(i * len(parts) / 4) for i in range(4)})
                subs = await asyncio.gather(*(summarize.summarize_page(
                    parts[i], f"{u}#part={i + 1}", focus) for i in chosen))
                s = "\n".join(subs if len(subs) == 1 else
                              [f"(part {i + 1} of {len(parts)})\n{x}" for i, x in zip(chosen, subs)])
                note = webfallback.searched_note(chosen, len(parts))
                s += f"\n{note}" if note else ""
        except Exception as e:  # noqa: BLE001
            return f"Source: {u}\n(could not summarize: {e})"
        if had_image:
            s += "\n(this page is a scan: its image was not summarized; read it with web_read)"
        return f"Source: {u}\n{s}"

    blocks = await asyncio.gather(*(one(u) for u in ordered))
    return "\n\n".join(blocks)
