---
name: web_read
description: Fetch one web page and return it as inert plain text (all scripts, styles and markup stripped). Internal/private addresses are refused.
when_to_use: When you need ONE page's full text verbatim (e.g. exact quotes, code). If you are surveying several sources, or don't need the page word-for-word, use read_and_summarize instead — full pages left in context are the main driver of runaway token cost.
enabled: true
section: web
core: true
action: read
read_only: true
parameters:
  type: object
  properties:
    url:
      type: string
      description: A full http(s) URL.
    extract:
      type: string
      description: Optional — what to pull from the page; returns a focused extraction instead of the raw page text.
  required: [url]
---
You never touch the raw internet — the host fetches and sanitizes the page for
you. If a URL was already fetched this session it won't be pulled again; choose
a different source to keep the gathered knowledge diverse.

API keys: write {{secret:NAME}} in the URL (e.g. ...?apiKey={{secret:NEWSAPI}})
and the host substitutes the real value at fetch time — but only toward the
hosts the operator bound that secret to. You never see the value; responses
that echo it come back scrubbed.

A long page is never cut silently. The first part comes back with a closing
note, "(cut at 6,000 of 48,213 characters: read on with <url>#part=2, ...)".
Call web_read again with <url>#part=2, #part=3 and so on for the rest (the page
is downloaded once and kept for a while), or pass extract= to search the WHOLE
page: it looks through the parts that match your request best (up to four) and
says when it searched only some of them.

A page built by JavaScript, or one that is only menus, is not handed over as if
it were the page. web_read first looks for the text elsewhere: embedded in the
page (JSON-LD, Next.js/Nuxt data, noscript), an alternate the page links (AMP,
RSS/Atom, print version), then the Wayback Machine. A result read that way
starts with "(read from: ...)": check the source and the date. If nothing works
you get a "note:" saying what was tried. Then search for another copy of the
document by its title, or use headless_browser in a box with web access. Do not
conclude from the note that the page has no such content.

A PDF page with no text layer is a scan. The first read of a PDF says how many
pages are scans. Read one with `<url>#page=K` and its image comes attached, so a
chat model that can see images can read it; one page per read. For a range such
as #page=3-9, text pages come as text and the first scan page's image is
attached, with a line listing the remaining scan pages. If the chat model cannot
read images, the result says so: switch to a vision model or find a text copy.
#search= only searches text, so it misses scanned pages.
