"""The `think` event: the model's reasoning streams as its own event and never
lands in the message content."""
import json

import httpx

from backend.agent import adapters, loop as loop_mod, model as model_mod


def _sse(chunks):
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"

    def handler(request):
        return httpx.Response(200, content=body.encode(),
                              headers={"content-type": "text/event-stream"})
    return handler


def _patch_http(monkeypatch, chunks):
    t = httpx.MockTransport(_sse(chunks))
    monkeypatch.setattr(adapters, "client",
                        lambda: httpx.AsyncClient(transport=t))


def _d(**delta):
    return {"choices": [{"delta": delta}]}


async def _stream():
    m = model_mod.ModelClient(api_key="k")
    return [e async for e in m._stream_once("http://x", "k", {"stream": True})]


async def test_reasoning_content_then_content(tmp_env, monkeypatch):
    _patch_http(monkeypatch, [_d(reasoning_content="hmm "), _d(reasoning_content="ok"),
                              _d(content="Hello")])
    evs = await _stream()
    kinds = [e["type"] for e in evs]
    assert kinds[:2] == ["think", "think"] and "token" in kinds
    assert "".join(e["text"] for e in evs if e["type"] == "think") == "hmm ok"
    assert evs[-1]["content"] == "Hello"


async def test_reasoning_variant(tmp_env, monkeypatch):
    _patch_http(monkeypatch, [_d(reasoning="thinking"), _d(content="Hi")])
    evs = await _stream()
    assert evs[0] == {"type": "think", "text": "thinking"}
    assert evs[-1]["content"] == "Hi"


async def test_client_passes_think_and_final(tmp_env, monkeypatch):
    async def fake(self, base, key, payload):
        yield {"type": "think", "text": "abc"}
        yield {"type": "token", "text": "yo"}
        yield {"type": "raw", "content": "yo", "tool_calls": [], "usage": None}
    monkeypatch.setattr(model_mod.ModelClient, "_stream_once", fake)
    m = model_mod.ModelClient(api_key="k")
    evs = [e async for e in m.complete([{"role": "user", "content": "x"}])]
    assert [e["type"] for e in evs][:2] == ["think", "token"]
    assert evs[-1]["type"] != "think" and evs[-1]["content"] == "yo"


async def test_gateway_passes_think_and_final(tmp_env, monkeypatch):
    async def fake(self, base, key, payload):
        yield {"type": "think", "text": "abc"}
        yield {"type": "raw", "content": "yo", "tool_calls": [], "usage": None}
    monkeypatch.setattr(model_mod.ModelClient, "_stream_once", fake)
    g = model_mod.ModelGateway(api_key="k")
    evs = [e async for e in g.complete([{"role": "user", "content": "x"}])]
    assert evs[0] == {"type": "think", "text": "abc"}
    assert evs[-1]["type"] != "think"


async def test_retry_after_think(tmp_env, monkeypatch):
    calls = {"n": 0}

    async def once():
        calls["n"] += 1
        if calls["n"] == 1:
            yield {"type": "think", "text": "partial"}
            raise httpx.ReadError("drop")
        yield {"type": "raw", "content": "ok"}

    async def nosleep(*a, **k):
        return None
    monkeypatch.setattr(adapters.asyncio, "sleep", nosleep)
    evs = [e async for e in adapters.retrying(once)]
    assert [e["type"] for e in evs] == ["think", "retry", "raw"]


def test_loop_site_passthrough():
    import inspect
    src = inspect.getsource(loop_mod)
    assert src.count('("token", "think", "retry")') >= 2


def test_guest_client_passthrough():
    from pathlib import Path
    src = (Path(__file__).parent.parent / "guest/backend/agent/model.py").read_text()
    assert '("token", "think", "retry")' in src
