"""headless_browser: the CDP pipe framing, element list, key table and argv are
pure; one real session runs against the Mac's Chrome (skipped when absent)."""
import asyncio
import functools
import http.server
import importlib.util
import json
import os
import threading

import pytest

from backend import headless
from backend.config import settings

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def _load_handler():
    spec = importlib.util.spec_from_file_location(
        "t_headless_handler", settings.base_dir / "tools" / "headless_browser" / "handler.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- pure -------------------------------------------------------------------------

def test_frame_roundtrip_and_partial_tail():
    a, b = {"id": 1, "method": "X"}, {"id": 2, "result": {"é": "ü"}}
    raw = headless.frame(a) + headless.frame(b)
    assert raw.endswith(b"\x00") and raw.count(b"\x00") == 2
    msgs, rest = headless.split_frames(raw)
    assert msgs == [a, b] and rest == b""
    msgs, rest = headless.split_frames(raw[:-5])      # second message cut short
    assert msgs == [a] and rest == raw[len(headless.frame(a)):-5]
    msgs2, rest2 = headless.split_frames(rest + raw[-5:])
    assert msgs2 == [b] and rest2 == b""


def test_split_frames_drops_garbage():
    msgs, rest = headless.split_frames(b'not json\x00\x00[1]\x00{"id":3}\x00{"id"')
    assert msgs == [{"id": 3}] and rest == b'{"id"'


def test_element_lines():
    els = [{"tag": "button", "role": "", "label": "  Add\n one ", "x": 10.4, "y": 20},
           {"tag": "a", "role": "link", "label": "Home", "x": 5, "y": 6}]
    assert headless.element_line(1, els[0]) == '[1] button "Add one" (10,20)'
    assert headless.element_line(2, els[1]) == '[2] a/link "Home" (5,6)'
    block = headless.element_block(els * 40)
    assert block.count("\n[") == 60 and "untrusted" in block.splitlines()[0]
    assert "none found" in headless.element_block([])


def test_console_errors_first_and_capped():
    items = [(False, f"log {i}") for i in range(10)] + [(True, "x.js:1 Uncaught boom"),
                                                          (True, "x.js:1 Uncaught boom")]
    out = headless.console_lines(items)
    assert out[0] == "x.js:1 Uncaught boom" and len(out) == 8
    assert out.count("x.js:1 Uncaught boom") == 1


def test_keys():
    assert headless.key_def("Enter")["windowsVirtualKeyCode"] == 13
    assert headless.key_def("ArrowLeft")["key"] == "ArrowLeft"
    d = headless.key_def("a")
    assert d["code"] == "KeyA" and d["text"] == "a" and d["windowsVirtualKeyCode"] == 65
    assert headless.key_def("7")["code"] == "Digit7"
    assert headless.key_def("space")["text"] == " "
    assert headless.key_def("Banana") is None and headless.key_def("") is None


def test_remote_url_rules():
    assert not headless.is_remote_url("http://localhost:8000/x")
    assert not headless.is_remote_url("http://127.0.0.1/")
    assert not headless.is_remote_url("http://[::1]:3000/")
    assert not headless.is_remote_url("data:text/html,hi")
    assert not headless.is_remote_url("about:blank")
    assert headless.is_remote_url("https://example.com/")
    assert headless.is_remote_url("http://10.0.0.5/")


def test_argv_flags():
    a = headless.chromium_argv("/usr/bin/chromium-headless-shell", "/p", 1280, 800,
                               "http://10.201.0.1:8443", root=True, headless_shell=True)
    assert "--headless=new" not in a and "--remote-debugging-pipe" in a
    assert "--use-angle=swiftshader" in a and "--enable-unsafe-swiftshader" in a
    assert "--no-sandbox" in a and "--proxy-server=http://10.201.0.1:8443" in a
    b = headless.chromium_argv("/usr/bin/chromium", "/p", 800, 600, "", root=False,
                               headless_shell=False)
    assert "--headless=new" in b and "--no-sandbox" not in b
    assert not any(x.startswith("--proxy-server") for x in b)
    assert sum(x.startswith("--disable-features=") for x in a) == 1


def test_cap_value_and_changed_line():
    assert headless.cap_value({"a": 1}) == '{"a": 1}'
    assert headless.cap_value("x" * 5000).endswith("[cut at 4096 bytes]")
    assert headless.changed_line(("u", 10, 1), ("u", 10, 1)).startswith("changed: nothing")
    c = headless.changed_line(("u", 10, 1), ("v", 12, 2))
    assert "url" in c and "dom 10 -> 12" in c and "pixels" in c


# --- the handler without a browser -------------------------------------------------

def test_handler_refuses_outside_a_box(monkeypatch):
    h = _load_handler()
    monkeypatch.setattr(settings, "in_guest", False, raising=False)
    assert "only runs inside" in asyncio.run(h.run(action="open", url="http://localhost"))


def test_handler_needs_open_first(monkeypatch):
    h = _load_handler()
    monkeypatch.setattr(settings, "in_guest", True, raising=False)
    headless.new_session()
    assert "Use action=\"open\"" in asyncio.run(h.run(action="click", x=1, y=1))
    assert asyncio.run(h.run(action="nope")).startswith("error: action must be")


def test_tool_is_registered_in_the_box_lists():
    from backend.agent.tools import inguest
    from backend.vm import guest_pkg
    assert "headless_browser" in inguest.IN_GUEST_TOOLS
    assert "headless_browser" in inguest.BOX_ONLY_TOOLS
    assert "backend/headless.py" in guest_pkg._COPY_MODULES
    import tarfile
    import io
    monkey = settings.vm_boxes_enabled
    try:
        settings.vm_boxes_enabled = True
        names = tarfile.open(fileobj=io.BytesIO(guest_pkg.build_package_tar())).getnames()
    finally:
        settings.vm_boxes_enabled = monkey
    assert "tools/headless_browser/handler.py" in names and "backend/headless.py" in names


# --- one real session ----------------------------------------------------------------

PAGE = """<!doctype html><title>t</title><body style="margin:0;background:#fff">
<button id=b onclick="n++;this.textContent='clicked '+n" style="position:absolute;left:20px;top:20px;width:160px;height:40px">go</button>
<input id=pw type=password style="position:absolute;left:20px;top:80px">
<input id=tx style="position:absolute;left:20px;top:120px">
<canvas id=c width=300 height=300 style="position:absolute;left:300px;top:20px"></canvas>
<script>
var n=0, keys=[];
addEventListener('keydown',e=>keys.push(e.key));
var gl=document.getElementById('c').getContext('webgl2');
window.glok=!!gl;
if(gl){
 var vs=gl.createShader(gl.VERTEX_SHADER);gl.shaderSource(vs,'#version 300 es\\nin vec2 p;void main(){gl_Position=vec4(p,0,1);}');gl.compileShader(vs);
 var fs=gl.createShader(gl.FRAGMENT_SHADER);gl.shaderSource(fs,'#version 300 es\\nprecision mediump float;out vec4 o;void main(){o=vec4(1,0,0,1);}');gl.compileShader(fs);
 var pr=gl.createProgram();gl.attachShader(pr,vs);gl.attachShader(pr,fs);gl.linkProgram(pr);gl.useProgram(pr);
 var b=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,b);
 gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-0.8,-0.8,0.8,-0.8,0,0.8]),gl.STATIC_DRAW);
 var l=gl.getAttribLocation(pr,'p');gl.enableVertexAttribArray(l);gl.vertexAttribPointer(l,2,gl.FLOAT,false,0,0);
 gl.clearColor(0,0,1,1);gl.clear(gl.COLOR_BUFFER_BIT);gl.drawArrays(gl.TRIANGLES,0,3);
}
console.error('hello from the page');
</script></body>"""


@pytest.fixture
def server(tmp_path):
    (tmp_path / "index.html").write_text(PAGE)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/index.html"
    srv.shutdown()


@pytest.mark.skipif(not os.path.exists(CHROME), reason="no Chrome on this machine")
def test_real_session(server, monkeypatch, tmp_path):
    Image = pytest.importorskip("PIL.Image")
    monkeypatch.setattr(headless, "find_binary", lambda: (CHROME, False))
    h = _load_handler()
    monkeypatch.setattr(h, "SHOT_DIR", str(tmp_path / "shots"))
    monkeypatch.setattr(settings, "in_guest", True, raising=False)
    shot_mod = h._screenshot_mod()
    monkeypatch.setattr(shot_mod, "proxy_addr", lambda: ("", "", 0))
    headless.new_session()

    async def go():
        from backend.agent import imageresult
        try:
            out = await h.run(action="open", url=server, wait_ms=500)
            assert "opened" in out and "hello from the page" in out, out
            assert 'button "go"' in out and "input:password" in out
            assert "[remote content" not in out
            first = out
            # click the button by its element number
            n = [ln for ln in first.splitlines() if 'button "go"' in ln][0].split("]")[0][1:]
            out = await h.run(action="click", element=int(n))
            assert "changed:" in out and "pixels" in out
            out = await h.run(action="eval", expression="n")
            assert ": 1" in out.splitlines()[0]
            # a password field is refused
            out = await h.run(action="type", selector="#pw", text="hunter2")
            assert "password" in out and out.startswith("error")
            out = await h.run(action="type", selector="#tx", text="abc")
            assert "typed 3" in out
            assert "abc" in (await h.run(action="eval", expression="document.getElementById('tx').value"))
            await h.run(action="key", key="a", hold_ms=100)
            assert '"a"' in (await h.run(action="eval", expression="JSON.stringify(keys)"))
            # a stale number after navigating is refused
            await h.run(action="eval", expression="location.href='about:blank'")
            out = await h.run(action="click", element=int(n))
            assert out.startswith("error") and "read again" in out
            # WebGL: the triangle is drawn, not blank
            await h.run(action="open", url=server, wait_ms=500)
            assert "true" in await h.run(action="eval", expression="window.glok")
            out = await h.run(action="shot")
            assert imageresult.MARKER in out
            return out
        finally:
            await h.run(action="close")

    out = asyncio.run(go())
    from backend.agent import imageresult
    path = json.loads(out.split(imageresult.MARKER, 1)[1])["path"]
    im = Image.open(path).convert("RGB")
    # the canvas sits at (300,20)..(600,320): the triangle centre is red on blue
    reds = sum(1 for x in range(320, 580, 10) for y in range(40, 300, 10)
               if im.getpixel((x, y))[0] > 200 and im.getpixel((x, y))[2] < 80)
    assert reds > 50, f"no triangle drawn ({reds} red samples)"
