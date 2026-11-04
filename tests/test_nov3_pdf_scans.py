"""web_read on a PDF with scanned pages (backend/pdftext.py): the first read counts
the scans, #page=K attaches the page's image, a range attaches the first one,
and a chat model that cannot see images is told so. 2026-11-03: a 15-page
scanned PDF used to read as fifteen empty pages."""
import io
import random

import httpx
import pytest

pypdf = pytest.importorskip("pypdf")
pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from backend import pdftext, webtools  # noqa: E402
from backend.agent import imageresult  # noqa: E402
from backend.db import init_db  # noqa: E402
from tests.test_pdftext import make_pdf  # noqa: E402

URL = "https://example.test/doc.pdf"


def scan_pdf(size=(300, 400)) -> bytes:
    rnd = random.Random(1)
    im = Image.new("L", size)
    im.putdata([rnd.randrange(256) for _ in range(size[0] * size[1])])
    buf = io.BytesIO()
    im.save(buf, "PDF")
    return buf.getvalue()


def mixed() -> bytes:
    """page 1 text, pages 2 and 3 scans, page 4 text."""
    w = pypdf.PdfWriter()
    for raw in (make_pdf(["First page text"]), scan_pdf(), scan_pdf((200, 200)),
                make_pdf(["Last page text"])):
        w.append(pypdf.PdfReader(io.BytesIO(raw)))
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(webtools, "_page_cache", {})
    monkeypatch.setattr(pdftext, "_docs", {})
    monkeypatch.setattr(pdftext, "_can_see", lambda: True)


async def load(raw=None):
    return await pdftext.load(URL, raw or mixed())


async def test_scan_pages_are_found():
    doc = await load()
    assert doc["scans"] == [2, 3]
    assert doc["raw"]


async def test_text_only_pdf_keeps_no_bytes():
    doc = await pdftext.load(URL, make_pdf(["a", "b"]))
    assert doc["scans"] == [] and doc["raw"] is None


async def test_first_read_counts_scans():
    doc = await load()
    text = await pdftext.answer(URL, doc, None, None, 12000)
    assert "2 of 4 pages are scans" in text and f"{URL}#page=2" in text
    assert imageresult.MARKER not in text


async def test_single_scan_page_attaches_png():
    doc = await load()
    out = await pdftext.answer(URL, doc, "page", "2", 12000)
    text, img = imageresult.split(out)
    assert "Page 2 is a scan" in text and "attached" in text
    assert img.caption == f"page 2 of {URL} (scan)"
    assert imageresult.sniff(img.data(5_000_000)) == "image/png"


async def test_big_scan_is_downscaled_and_capped():
    doc = await pdftext.load(URL, scan_pdf((3000, 2000)))
    data, mime = await pdftext.page_image(doc, 1)
    assert len(data) <= pdftext.IMG_BYTES
    assert max(Image.open(io.BytesIO(data)).size) <= pdftext.IMG_SIDE


async def test_range_attaches_first_scan_and_lists_the_rest():
    doc = await load()
    out = await pdftext.answer(URL, doc, "page", "1-4", 12000)
    text, img = imageresult.split(out)
    assert "First page text" in text and "Last page text" in text
    assert img.caption.startswith("page 2 of")
    assert f"{URL}#page=3" in text


async def test_text_page_has_no_image():
    doc = await load()
    out = await pdftext.answer(URL, doc, "page", "1", 12000)
    assert imageresult.MARKER not in out


async def test_model_without_vision_is_told(monkeypatch):
    monkeypatch.setattr(pdftext, "_can_see", lambda: False)
    doc = await load()
    out = await pdftext.answer(URL, doc, "page", "2", 12000)
    assert imageresult.MARKER not in out
    assert "cannot read images" in out


async def test_search_stays_text():
    doc = await load()
    out = await pdftext.answer(URL, doc, "search", "text", 12000)
    assert imageresult.MARKER not in out


async def test_undecodable_image_says_why(monkeypatch):
    doc = await load()
    monkeypatch.setattr(pdftext, "_page_image",
                        lambda raw, n: "the scan is in an image format this server cannot decode")
    out = await pdftext.answer(URL, doc, "page", "2", 12000)
    assert "could not be attached" in out and "cannot decode" in out


async def test_raw_bytes_are_dropped_when_the_cache_is_full(monkeypatch):
    monkeypatch.setattr(pdftext, "RAW_TOTAL", 1)
    doc = await load()
    assert doc["raw"] is None
    out = await pdftext.answer(URL, doc, "page", "2", 12000)
    assert "no longer held" in out


async def test_read_attaches_the_image_and_does_not_cache_it(tmp_env, monkeypatch):
    # tmp_env: read() claims the URL in the DB ledger (without it the claim went into
    # the real state dir, and a second run found its own claim)
    await init_db()
    raw = mixed()

    def handler(req):
        return httpx.Response(200, content=raw, headers={"content-type": "application/pdf"})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(webtools, "is_safe_url", lambda u: None)
    out = await webtools.read(URL + "#page=2", "s1")
    text, img = imageresult.split(out)
    assert img is not None and "scan" in text
    assert webtools._page_cache == {}
    again = await webtools.read(URL + "#page=2", "s1")   # the parsed document answers
    assert again.count(imageresult.MARKER) == 1
    assert "served from cache" not in again
