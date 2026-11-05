"""The pages server: published snapshots, read-only, on a port of its own.

`python -m backend.pages_server` (its own systemd user unit, scripts/
jarvis-pages.service). It serves <data_dir>/pages/<id>/ at /p/<id>/<path> and
knows nothing else: no database, no project folder, no login. Anyone with the
link opens the page; the link is the secret (secrets.token_urlsafe(12)).

Why it is a separate server and not a route of the app: cookies are not
port-scoped, so a page on :8090 is same-site with the app on :8000. Every
response therefore carries a CSP `sandbox` (without allow-same-origin): the page
gets an opaque origin and cannot use the operator's session. The server also
never sets a cookie, and ignores the Cookie header (it is never read or logged).

GET and HEAD only. An unknown id and an unknown file get the same 404. Types
come from a fixed map by extension; anything else downloads as
application/octet-stream.
"""
import html
import re
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from starlette.routing import Route

from .config import settings

SECURITY_HEADERS = {
    "Content-Security-Policy": ("sandbox allow-scripts allow-popups "
                                "allow-popups-to-escape-sandbox allow-downloads; "
                                "frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}

TYPES = {
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json", ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8", ".csv": "text/csv; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    ".pdf": "application/pdf", ".mp4": "video/mp4", ".webm": "video/webm",
    ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav",
}

_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
NOT_FOUND = "not found\n"


def _not_found() -> Response:
    return PlainTextResponse(NOT_FOUND, status_code=404)


def _listing(pid: str, rel: str, d: Path) -> Response:
    rows = sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    items = "".join(
        f'<li><a href="{html.escape(p.name)}{"/" if p.is_dir() else ""}">'
        f'{html.escape(p.name)}{"/" if p.is_dir() else ""}</a></li>' for p in rows)
    title = html.escape(f"/{rel}" if rel else "/")
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>{title}</title>"
                        f"<h1>{title}</h1><ul>{items}</ul>")


async def serve(request: Request) -> Response:
    if request.method not in ("GET", "HEAD"):
        return PlainTextResponse("method not allowed\n", status_code=405,
                                 headers={"Allow": "GET, HEAD"})
    raw = request.url.path
    parts = raw.split("/")                  # "", "p", id, ...
    if len(parts) < 3 or parts[1] != "p" or not _ID.match(parts[2]):
        return _not_found()
    pid, rest = parts[2], parts[3:]
    if any(s in ("..", ".") or "\\" in s or "\x00" in s for s in rest):
        return _not_found()
    base = (settings.data_dir / "pages" / pid)
    if not base.is_dir():
        return _not_found()
    names = [s for s in rest if s]
    target = base.joinpath(*names)
    try:
        real_base, real = base.resolve(), target.resolve()
    except OSError:
        return _not_found()
    if not real.is_relative_to(real_base) or target.is_symlink():
        return _not_found()
    if real.is_dir():
        if not raw.endswith("/"):           # relative links in the page need the slash
            return Response(status_code=307, headers={"Location": raw + "/"})
        index = real / "index.html"
        if index.is_file() and not index.is_symlink():
            real = index
        else:
            return _listing(pid, "/".join(names), real)
    if not real.is_file():
        return _not_found()
    ctype = TYPES.get(real.suffix.lower())
    if ctype is None:
        return FileResponse(real, media_type="application/octet-stream",
                            filename=real.name, content_disposition_type="attachment")
    return FileResponse(real, media_type=ctype)


class _Guard:
    """ASGI wrapper: the security headers on EVERY response (errors, redirects,
    405s included), and no Set-Cookie ever."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def guarded(msg):
            if msg["type"] == "http.response.start":
                hdrs = [(k, v) for k, v in msg.get("headers", [])
                        if k.lower() not in (b"set-cookie", *(h.lower().encode()
                                                              for h in SECURITY_HEADERS))]
                hdrs += [(k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]
                msg = {**msg, "headers": hdrs}
            await send(msg)

        await self.app(scope, receive, guarded)


_routes = [Route("/{path:path}", serve, methods=["GET", "HEAD", "POST", "PUT", "PATCH",
                                                  "DELETE", "OPTIONS"])]
app = _Guard(Starlette(routes=_routes))


def main() -> None:
    import uvicorn
    # access_log off: nothing about who opened what is kept, and a request's
    # headers (the Cookie one above all) are never written anywhere.
    uvicorn.run(app, host=settings.pages_bind, port=settings.pages_port,
                access_log=False, proxy_headers=False, server_header=False)


if __name__ == "__main__":
    main()
