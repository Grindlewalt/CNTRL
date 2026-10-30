"""The loop's `usage` event: one per model call, between the tokens and the tool calls."""
from backend.agent import loop


def test_usage_event_fields():
    ev = loop.usage_event({"prompt_tokens": 1200, "completion_tokens": 40,
                           "prompt_cache_hit_tokens": 1000}, 812.6, "deepseek-flash")
    assert ev == {"type": "usage", "in": 1200, "out": 40, "cached": 1000,
                  "ms": 812, "model": "deepseek-flash"}


def test_usage_event_cached_from_details_or_null():
    ev = loop.usage_event({"prompt_tokens": 5, "completion_tokens": 1,
                           "prompt_tokens_details": {"cached_tokens": 3}}, 1, "m")
    assert ev["cached"] == 3
    assert loop.usage_event({"prompt_tokens": 5}, 1, "m")["cached"] is None
    assert loop.usage_event(None, 1, "m") is None
