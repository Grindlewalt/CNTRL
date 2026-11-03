# nov3: web tools that say when they did not work (contract)

Base: branch `nov3` at the commit that adds this file. Every agent starts with
`git reset --hard <that sha>` and touches ONLY the files it owns.

Why: Pi chat #723 (2026-11-03). Every call "succeeded" and most returned junk:
SearXNG had one engine answering (Bing, with unrelated pages), web_read cut
pages at 6,000 chars without saying so, a JavaScript page read as its menu, a
scanned PDF had no text, and the model built a debate brief on what was left.
The rule for all of this: a tool says plainly when its result is degraded, what
it tried, and what to do next. It never hands junk over as a result.

## Ownership

| Agent | Owns | Must not touch |
|---|---|---|
| S search | `backend/webtools.py`: `_search_params`, `search_results`, `search` and new helpers placed right above `_search_params`; `backend/research.py`; `tools/web_search/*`; `tools/research/*`; the doctor's search check; tests `tests/test_nov3_search.py` | `read()` and below in webtools.py |
| W pages | `backend/webtools.py`: `read()` EXCEPT the PDF branches (the `frag` early return and the two `pdftext.looks_like_pdf` blocks), `html_to_text` and helpers below `read`; new `backend/webfallback.py`; `tools/web_read/*`; `tools/read_and_summarize/*`; tests `tests/test_nov3_pages.py` | the PDF branches, search functions |
| P pdf | `backend/pdftext.py`; inside `webtools.read()` ONLY the `frag` early return and the two `looks_like_pdf` blocks; tests `tests/test_nov3_pdf_scans.py` | everything else in webtools.py; `tools/web_read/TOOL.md` (send its doc lines in the report instead) |
| J journal | `backend/memory.py` journal functions, `tools/journal_update/*`, a new `backend/journalhold.py` if wanted, the API route(s) for it, the web Queue UI for it, the TUI Queue rows for it (`clients/jav3cli/jav3`); tests `tests/test_nov3_journal_hold.py` | web tools |
| X tools | `tools/screenshot/*` (and its guest-side counterpart if any), the call-log action (a `system` section action, new `tools/<name>/`); tests `tests/test_nov3_screenshot_path.py`, `tests/test_nov3_call_log.py` | web tools, journal |

## Shared formats

Degraded search, the one line `search()` puts right after its header lines and
that research and the doctor reuse (helper `search_health(data) -> str | None`
in webtools.py, owned by S):

    (search is degraded: 1 of 3 engines answered; mojeek: suspended: access denied; ...)

A thin or cut page (W) ends its text with one note line in parentheses, e.g.

    (cut at 6,000 of 48,213 characters: read on with <url>#part=2, or ask web_read
     with extract= to search the whole page)

A page read from somewhere other than its URL starts with

    (read from: the Wayback Machine copy of 2023-05-01, <archive url>)

## Rules every agent keeps

- Tests never touch the network: fakes and httpx.MockTransport only.
- Fallback fetches go through the same SSRF-guarded fetch as the page itself,
  are untrusted content like it (same taint), and never run when the URL had a
  `{{secret:...}}` substituted (a secret never reaches a third party).
- No browser on the host. Rendering a page is a box's job (headless_browser).
- Keep tool outputs small; commit on your branch; report what you verified.
