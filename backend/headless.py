"""A headless browser the agent drives over the Chrome DevTools Protocol.

Copied verbatim into the guest (guest_pkg._COPY_MODULES) like memguard; the
`headless_browser` tool (tools/headless_browser/handler.py) is its only caller.
Standard library only: no websocket library, no Playwright, no Pillow.

The browser is started with `--remote-debugging-pipe`: the protocol runs over
two inherited pipes, fd 3 (commands in) and fd 4 (replies and events out), each
message a JSON text ended by one NUL byte. There is ONE page session per box. It
closes after IDLE_S idle seconds, on `close`, and when chromium dies.

WebGL: the box has no GPU, so the page is drawn by SwiftShader (the software
Vulkan that ANGLE falls back to). Chromium 150/154 headless needs
`--use-angle=swiftshader --enable-unsafe-swiftshader`; without them
getContext("webgl2") returns null. Proved with a WebGL2 triangle page in
tests/test_headless_browser.py.
"""
import asyncio
import base64
import fcntl
import json
import os
import shutil
import tempfile
import zlib
from urllib.parse import urlsplit

IDLE_S = 300
START_S = 30
CMD_S = 30
LOAD_S = 20
ELEMENT_MAX = 60
EVAL_MAX = 4096
CONSOLE_MAX = 8
SHOT_W_MAX = 1280
FULL_PAGE_H = 4000
HOLD_MAX_MS = 10_000
TEXT_MAX = 2000
HEADLESS_BINARIES = ("chromium-headless-shell", "headless_shell")
FULL_BINARIES = ("chromium", "chromium-browser")


# --- pure helpers (tested on the host) -----------------------------------------

def frame(msg: dict) -> bytes:
    """One CDP message as it goes down the pipe: JSON, then a NUL."""
    return json.dumps(msg, separators=(",", ":")).encode() + b"\x00"


def split_frames(buf: bytes) -> tuple[list[dict], bytes]:
    """(complete messages, the unfinished tail). A frame that is not JSON is
    dropped: the protocol never sends one and a stray byte must not end the session."""
    *done, rest = buf.split(b"\x00")
    out = []
    for raw in done:
        if not raw.strip():
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if isinstance(msg, dict):
            out.append(msg)
    return out, rest


def clamp_int(v, default: int, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(n, hi))


def is_loopback_host(host: str) -> bool:
    import ipaddress
    host = (host or "").lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def is_remote_url(url: str) -> bool:
    """True for a request that leaves the box. about:, data:, blob: and
    chrome-error: pages never touch the network."""
    p = urlsplit(url or "")
    if p.scheme not in ("http", "https", "ws", "wss", "ftp"):
        return False
    return not is_loopback_host(p.hostname or "")


def element_line(n: int, e: dict) -> str:
    """`[N] tag/role "label" (x,y)`."""
    kind = e.get("tag", "?")
    if e.get("role") and e["role"] != kind:
        kind += "/" + e["role"]
    label = " ".join(str(e.get("label", "")).split())[:60]
    return f'[{n}] {kind} "{label}" ({int(e.get("x", 0))},{int(e.get("y", 0))})'


def element_block(els: list[dict]) -> str:
    if not els:
        return "interactive elements: none found"
    lines = [element_line(i + 1, e) for i, e in enumerate(els[:ELEMENT_MAX])]
    return "interactive elements (page text, untrusted data):\n" + "\n".join(lines)


def console_lines(items: list[tuple[bool, str]]) -> list[str]:
    """[(is_error, text)] -> at most CONSOLE_MAX lines, uncaught errors first,
    duplicates dropped."""
    seen, errs, rest = set(), [], []
    for is_err, text in items:
        text = " ".join(str(text).split())[:240]
        if text in seen:
            continue
        seen.add(text)
        (errs if is_err else rest).append(text)
    return (errs + rest)[:CONSOLE_MAX]


def cap_value(v) -> str:
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    if len(s.encode()) <= EVAL_MAX:
        return s
    return s.encode()[:EVAL_MAX].decode("utf-8", "ignore") + f"... [cut at {EVAL_MAX} bytes]"


# key name -> (key, code, windowsVirtualKeyCode, text)
KEYS = {
    "enter": ("Enter", "Enter", 13, "\r"), "return": ("Enter", "Enter", 13, "\r"),
    "tab": ("Tab", "Tab", 9, ""), "escape": ("Escape", "Escape", 27, ""),
    "esc": ("Escape", "Escape", 27, ""), "backspace": ("Backspace", "Backspace", 8, ""),
    "delete": ("Delete", "Delete", 46, ""), "space": (" ", "Space", 32, " "),
    "arrowup": ("ArrowUp", "ArrowUp", 38, ""), "up": ("ArrowUp", "ArrowUp", 38, ""),
    "arrowdown": ("ArrowDown", "ArrowDown", 40, ""), "down": ("ArrowDown", "ArrowDown", 40, ""),
    "arrowleft": ("ArrowLeft", "ArrowLeft", 37, ""), "left": ("ArrowLeft", "ArrowLeft", 37, ""),
    "arrowright": ("ArrowRight", "ArrowRight", 39, ""), "right": ("ArrowRight", "ArrowRight", 39, ""),
    "home": ("Home", "Home", 36, ""), "end": ("End", "End", 35, ""),
    "pageup": ("PageUp", "PageUp", 33, ""), "pagedown": ("PageDown", "PageDown", 34, ""),
}


def key_def(name: str) -> dict | None:
    """CDP Input.dispatchKeyEvent fields for a key name or one character."""
    if not isinstance(name, str) or not name:
        return None
    hit = KEYS.get(name.lower())
    if hit:
        key, code, vk, text = hit
        return {"key": key, "code": code, "windowsVirtualKeyCode": vk, "text": text}
    if len(name) == 1:
        up = name.upper()
        if name.isalpha():
            code = "Key" + up
        elif name.isdigit():
            code = "Digit" + name
        else:
            code = ""
        vk = ord(up) if name.isalnum() else ord(name)
        return {"key": name, "code": code, "windowsVirtualKeyCode": vk, "text": name}
    return None


def chromium_argv(binary: str, profile_dir: str, width: int, height: int, proxy: str,
                  *, root: bool, headless_shell: bool) -> list[str]:
    """The flags of screenshot.chromium_argv (egress proxy, Google traffic off,
    --no-sandbox only as root) plus the pipe and SwiftShader WebGL. Kept as its
    own list: screenshot's is built around --screenshot and a virtual-time budget."""
    off = ",".join(("OptimizationHints", "OptimizationGuideModelDownloading",
                    "OptimizationHintsFetching", "OptimizationTargetPrediction",
                    "MediaRouter", "Translate", "NetworkTimeServiceQuerying",
                    "AutofillServerCommunication", "CertificateTransparencyComponentUpdater"))
    argv = [binary]
    if not headless_shell:
        argv.append("--headless=new")
    argv += ["--remote-debugging-pipe",
             # no GPU in the box: ANGLE's software Vulkan draws WebGL
             "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
             "--ignore-gpu-blocklist", "--hide-scrollbars",
             "--no-first-run", "--no-default-browser-check", "--disable-extensions",
             "--disable-dev-shm-usage", "--disable-background-networking",
             "--disable-sync", "--metrics-recording-only", "--mute-audio",
             "--disable-component-update", "--component-updater=url-source=http://127.0.0.1:9/",
             "--disable-domain-reliability", "--no-pings", "--disable-breakpad",
             "--disable-client-side-phishing-detection", "--disable-default-apps",
             f"--disable-features={off}",
             f"--user-data-dir={profile_dir}", f"--window-size={width},{height}"]
    if proxy:
        argv.append(f"--proxy-server={proxy}")
    if root:
        argv.append("--no-sandbox")
    argv.append("about:blank")
    return argv


def find_binary() -> tuple[str, bool] | None:
    """(path, is_headless_shell). Prefers the headless shell."""
    for n in HEADLESS_BINARIES:
        p = shutil.which(n)
        if p:
            return p, True
    for n in FULL_BINARIES:
        p = shutil.which(n)
        if p:
            return p, False
    return None


# --- in-page scripts --------------------------------------------------------------

# Lists the visible interactive elements in the viewport, remembers them in
# window.__jv3 (so a click by number finds the same node), and returns plain data.
JS_ELEMENTS = r"""(() => {
  const sel = 'a[href],button,input,select,textarea,summary,[role=button],[role=link],[role=checkbox],[role=tab],[role=menuitem],[onclick],[tabindex]:not([tabindex="-1"]),[contenteditable=""],[contenteditable=true]';
  const out = [], nodes = [];
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.right < 0 ||
        r.top > innerHeight || r.left > innerWidth) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || cs.pointerEvents === 'none') continue;
    if (el.type === 'hidden') continue;
    const label = el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder ||
      el.title || el.alt || el.name || el.id || '';
    out.push({tag: el.tagName.toLowerCase() + (el.type && el.tagName === 'INPUT' ? ':' + el.type : ''),
      role: el.getAttribute('role') || '', label: String(label).trim().slice(0, 80),
      x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)});
    nodes.push(el);
    if (out.length >= %d) break;
  }
  window.__jv3 = nodes;
  return out;
})()""" % ELEMENT_MAX

# Resolves a target to a node, scrolls it into view and returns {x,y,tag,type} (or
# {error}). `how` is 'n' (index into window.__jv3), 'sel' or 'text'.
JS_RESOLVE = r"""((how, v) => {
  let el = null;
  if (how === 'n') {
    if (!window.__jv3) return {error: 'stale'};
    el = window.__jv3[v - 1];
  } else if (how === 'sel') {
    try { el = document.querySelector(v); } catch (e) { return {error: 'bad selector: ' + e.message}; }
  } else {
    const want = String(v).toLowerCase().trim();
    let best = null, bestLen = 1e9;
    for (const c of document.querySelectorAll('a,button,input,select,textarea,summary,label,[role],[onclick],h1,h2,h3,h4,li,span,div,p')) {
      const t = ((c.innerText || c.value || c.getAttribute('aria-label') || '') + '').toLowerCase().trim();
      if (!t || !t.includes(want) || t.length >= bestLen) continue;
      const r = c.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) continue;
      best = c; bestLen = t.length;
    }
    el = best;
  }
  if (!el || !el.isConnected) return {error: how === 'n' ? 'stale' : 'no match'};
  el.scrollIntoView({block: 'center', inline: 'center'});
  const r = el.getBoundingClientRect();
  return {x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
    tag: el.tagName.toLowerCase(), type: el.type || ''};
})"""

JS_FOCUS_CHECK = r"""(() => {
  const a = document.activeElement;
  return {type: (a && a.type) || '', tag: a ? a.tagName.toLowerCase() : ''};
})()"""


class BrowserError(Exception):
    """A failure the model can act on; the message says what to change."""


class Session:
    """The one page of this box. All methods are coroutines on one event loop."""

    def __init__(self):
        self.proc = None
        self.dead = ""              # why the browser is gone, "" while it runs
        self.profile = None
        self._wfile = None
        self._next = 0
        self._pending: dict = {}
        self._sid = None
        self._reader = None
        self._idle = None
        self.size = (1280, 800)
        # page state
        self.nav = 0                # bumps on every main-frame navigation
        self.elements_nav = -1      # `nav` when the element list was made
        self.elements: list[dict] = []
        self.console: list[tuple[bool, str]] = []
        self.remote = False         # any request left the box while this session lived
        self.noted_op = None        # the turn whose taint was last recorded
        self.last_hash = None
        self.last_dom = None
        self.last_url = ""
        self._load = asyncio.Event()

    # --- lifecycle --------------------------------------------------------------

    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None and not self.dead

    async def start(self, width: int, height: int, proxy: str) -> None:
        found = find_binary()
        if not found:
            raise BrowserError("no headless browser in this box (chromium-headless-shell or "
                               "chromium). Ask the operator for the `main` or `desktop` image.")
        binary, hs = found
        try:
            from backend import memguard
            memguard.setup()
            confine = memguard.confine
        except Exception:  # noqa: BLE001 -- no cgroup on a host test
            confine = None
        self.profile = tempfile.mkdtemp(prefix="jav3-hb-")
        c_r, c_w = os.pipe()        # we write commands to c_w, chromium reads fd 3 = c_r
        e_r, e_w = os.pipe()        # chromium writes fd 4 = e_w, we read e_r
        argv = chromium_argv(binary, self.profile, width, height, proxy,
                             root=os.geteuid() == 0, headless_shell=hs)

        def child():
            # park the ends above 3/4 first: either may already BE 3 or 4 here
            hi_r = fcntl.fcntl(c_r, fcntl.F_DUPFD, 10)
            hi_w = fcntl.fcntl(e_w, fcntl.F_DUPFD, 10)
            os.dup2(hi_r, 3)
            os.dup2(hi_w, 4)
            os.set_inheritable(3, True)
            os.set_inheritable(4, True)
            os.close(hi_r)
            os.close(hi_w)
            if confine:
                confine()
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL, start_new_session=True, preexec_fn=child,
                close_fds=False)    # it would close 3/4 after child() set them up; every
                                    # other fd of ours is close-on-exec anyway
        finally:
            os.close(c_r)
            os.close(e_w)
        self._wfile = os.fdopen(c_w, "wb", buffering=0)
        self.size = (width, height)
        loop = asyncio.get_running_loop()
        self._reader = loop.create_task(self._read(e_r))
        try:
            await self._attach()
        except Exception:
            await self.close()
            raise
        self.touch()

    async def _attach(self) -> None:
        tid = (await self._send("Target.createTarget", {"url": "about:blank"}))["targetId"]
        self._sid = (await self._send("Target.attachToTarget",
                                      {"targetId": tid, "flatten": True}))["sessionId"]
        for m in ("Page.enable", "Runtime.enable", "Network.enable", "Log.enable"):
            await self._cmd(m)
        await self._viewport(*self.size)

    async def _viewport(self, w: int, h: int) -> None:
        await self._cmd("Emulation.setDeviceMetricsOverride",
                        {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
        self.size = (w, h)

    def touch(self) -> None:
        if self._idle is not None:
            self._idle.cancel()
        loop = asyncio.get_running_loop()
        self._idle = loop.call_later(IDLE_S, lambda: loop.create_task(self.close()))

    async def close(self) -> None:
        if self._idle is not None:
            self._idle.cancel()
            self._idle = None
        self.dead = self.dead or "closed"
        if self.proc is not None and self.proc.returncode is None:
            try:
                os.killpg(self.proc.pid, 9)
            except OSError:
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except asyncio.TimeoutError:
                pass
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None
        if self._wfile is not None:
            try:
                self._wfile.close()
            except OSError:
                pass
            self._wfile = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(BrowserError("the browser closed"))
        self._pending.clear()
        if self.profile:
            shutil.rmtree(self.profile, ignore_errors=True)
            self.profile = None

    # --- protocol ---------------------------------------------------------------

    async def _read(self, fd: int) -> None:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        rf = os.fdopen(fd, "rb", buffering=0)
        transport, _ = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), rf)
        buf = b""
        try:
            while True:
                chunk = await reader.read(1 << 16)
                if not chunk:
                    break
                msgs, buf = split_frames(buf + chunk)
                for m in msgs:
                    self._on_message(m)
        except asyncio.CancelledError:
            raise
        finally:
            transport.close()
            if not self.dead:
                self.dead = "exited"
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(BrowserError("chromium exited"))
            self._pending.clear()
            self._load.set()

    def _on_message(self, m: dict) -> None:
        if "id" in m:
            fut = self._pending.pop(m["id"], None)
            if fut and not fut.done():
                if "error" in m:
                    fut.set_exception(BrowserError(str(m["error"].get("message", m["error"]))))
                else:
                    fut.set_result(m.get("result", {}))
            return
        self._event(m.get("method", ""), m.get("params") or {})

    def _event(self, method: str, p: dict) -> None:
        if method == "Runtime.exceptionThrown":
            d = p.get("exceptionDetails", {})
            ex = (d.get("exception") or {}).get("description") or d.get("text", "exception")
            src = (d.get("url") or "").split("?", 1)[0].rsplit("/", 1)[-1] or "page"
            self.console.append((True, f"{src}:{d.get('lineNumber', 0) + 1} Uncaught {ex.splitlines()[0]}"))
        elif method == "Runtime.consoleAPICalled":
            text = " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", []))
            self.console.append((p.get("type") in ("error", "assert"), f"console.{p.get('type')}: {text}"))
        elif method == "Log.entryAdded":
            e = p.get("entry", {})
            if e.get("level") == "error":
                self.console.append((True, f"{e.get('source', 'log')}: {e.get('text', '')} {e.get('url', '')}"))
        elif method == "Network.requestWillBeSent":
            if is_remote_url(p.get("request", {}).get("url", "")):
                self.remote = True
        elif method == "Page.frameNavigated":
            if not p.get("frame", {}).get("parentId"):
                self.nav += 1
        elif method == "Page.loadEventFired":
            self._load.set()
        elif method == "Inspector.targetCrashed":
            self.dead = "the page crashed"

    async def _send(self, method: str, params: dict | None = None, sid: str | None = None) -> dict:
        if self.dead or self._wfile is None:
            raise BrowserError(self.dead_text())
        self._next += 1
        msg = {"id": self._next, "method": method, "params": params or {}}
        if sid:
            msg["sessionId"] = sid
        fut = asyncio.get_running_loop().create_future()
        self._pending[self._next] = fut
        try:
            self._wfile.write(frame(msg))
        except OSError:
            raise BrowserError(self.dead_text()) from None
        try:
            return await asyncio.wait_for(fut, CMD_S)
        except asyncio.TimeoutError:
            # 2026-10-30, a WebGL game on the Pi: the 1280x800 capture timed out and the
            # same page answered at once at 800x500 (WebGL is drawn in software here)
            raise BrowserError(f"{method} did not answer in {CMD_S} s; the page is too busy "
                               "to draw (WebGL is software-rendered in the box, so a big "
                               "viewport is slow). Close the session and open it again "
                               "smaller, e.g. width 800 height 500.") from None

    async def _cmd(self, method: str, params: dict | None = None) -> dict:
        return await self._send(method, params, self._sid)

    def dead_text(self) -> str:
        return {"": "the browser is not running", "closed": "the browser session is closed",
                "exited": "chromium exited (probably killed for memory)"}.get(self.dead, self.dead)

    # --- page operations ----------------------------------------------------------

    async def evaluate(self, expr: str, *, await_promise: bool = True):
        r = await self._cmd("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": await_promise,
            "userGesture": True, "timeout": 10_000})
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            ex = (d.get("exception") or {}).get("description") or d.get("text", "exception")
            raise BrowserError("JS error: " + ex.splitlines()[0][:300])
        return r.get("result", {}).get("value")

    async def navigate(self, url: str) -> str:
        """Load `url`; returns "" or the network error text."""
        self._load.clear()
        r = await self._cmd("Page.navigate", {"url": url})
        if r.get("errorText"):
            return r["errorText"]
        try:
            await asyncio.wait_for(self._load.wait(), LOAD_S)
        except asyncio.TimeoutError:
            pass                    # a page that never finishes loading is still drawn
        if self.dead:
            raise BrowserError(self.dead_text())
        return ""

    async def list_elements(self) -> list[dict]:
        self.elements = await self.evaluate(JS_ELEMENTS) or []
        self.elements_nav = self.nav
        return self.elements

    async def resolve(self, how: str, value) -> dict:
        if how == "n" and self.elements_nav != self.nav:
            raise BrowserError("that element number is from before the page changed: use "
                               "headless_browser(action=\"read\") and read again.")
        r = await self.evaluate(f"({JS_RESOLVE})({json.dumps(how)}, {json.dumps(value)})")
        if not isinstance(r, dict) or r.get("error"):
            err = (r or {}).get("error", "no match") if isinstance(r, dict) else "no match"
            if err == "stale":
                raise BrowserError("that element is gone: use headless_browser(action=\"read\") "
                                   "and read again.")
            raise BrowserError(f"could not find the target ({err}).")
        return r

    async def mouse(self, x: float, y: float) -> None:
        for t, extra in (("mouseMoved", {}), ("mousePressed", {"button": "left", "clickCount": 1}),
                         ("mouseReleased", {"button": "left", "clickCount": 1})):
            await self._cmd("Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, **extra})

    async def wheel(self, dy: int) -> None:
        w, h = self.size
        await self._cmd("Input.dispatchMouseEvent", {
            "type": "mouseWheel", "x": w // 2, "y": h // 2, "deltaX": 0, "deltaY": dy})

    async def key(self, name: str, hold_ms: int = 0) -> None:
        d = key_def(name)
        if d is None:
            raise BrowserError(f"unknown key {name!r}: use Enter, Tab, Escape, Backspace, Space, "
                               "an arrow, Home/End/PageUp/PageDown, or one character.")
        down = {**d, "type": "keyDown" if d["text"] else "rawKeyDown"}
        await self._cmd("Input.dispatchKeyEvent", down)
        if hold_ms:
            await asyncio.sleep(min(hold_ms, HOLD_MAX_MS) / 1000)
        await self._cmd("Input.dispatchKeyEvent", {**d, "type": "keyUp", "text": ""})

    async def insert_text(self, text: str) -> None:
        await self._cmd("Input.insertText", {"text": text})

    async def screenshot(self, full_page: bool = False) -> tuple[bytes, tuple[int, int]]:
        w, h = self.size
        params = {"format": "jpeg", "quality": 80}
        height = h
        if full_page:
            m = await self._cmd("Page.getLayoutMetrics")
            cs = m.get("cssContentSize") or m.get("contentSize") or {}
            height = max(h, min(int(cs.get("height", h)), FULL_PAGE_H))
            params["captureBeyondViewport"] = True
        scale = min(1.0, SHOT_W_MAX / w)
        params["clip"] = {"x": 0, "y": 0, "width": w, "height": height, "scale": scale}
        r = await self._cmd("Page.captureScreenshot", params)
        return base64.b64decode(r["data"]), (int(w * scale), int(height * scale))

    async def state(self) -> tuple[str, str, int]:
        v = await self.evaluate(
            "({u: location.href, t: document.title, n: document.getElementsByTagName('*').length})")
        v = v or {}
        return v.get("u", ""), v.get("t", ""), int(v.get("n", 0))

    def take_console(self) -> list[str]:
        lines = console_lines(self.console)
        self.console = []
        return lines


def pixel_hash(data: bytes) -> int:
    return zlib.crc32(data)


def changed_line(before: tuple, after: tuple) -> str:
    """before/after = (url, dom_count, pixel_hash or None)."""
    what = []
    if before[0] != after[0]:
        what.append("url")
    if before[1] != after[1]:
        what.append(f"dom {before[1]} -> {after[1]} elements")
    if before[2] is not None and before[2] != after[2]:
        what.append("pixels")
    return "changed: " + (", ".join(what) if what else "nothing (same url, dom and pixels)")


session = Session()


def new_session() -> Session:
    """Replace the module's session (the old one must be closed)."""
    global session
    session = Session()
    return session
