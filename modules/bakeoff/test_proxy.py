"""Offline tests for bakeoff-proxy's stream accounting and outcome classes (run in the build)."""

import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path

os.environ["BAKEOFF_ROOT"] = tempfile.mkdtemp()
import proxy as p  # noqa: E402


def sse(*objs, done=True):
    out = b"".join(b"data: " + json.dumps(o).encode() + b"\n\n" for o in objs)
    return out + (b"data: [DONE]\n\n" if done else b"")


def chunk(delta=None, finish=None, **extra):
    o = {"model": "m", "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
    o.update(extra)
    return o


USAGE = {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 2},
         "billing_cost": {"credits": "0.0000271"}}


def run(raw, status=200, split=7):
    s = p.StreamSummary()
    for i in range(0, len(raw), split):   # arbitrary chunk boundaries
        s.feed(raw[i:i + split])
    return s, s.classify(status, True)


def test_ok_stop():
    s, c = run(sse(chunk({"content": "OK"}), chunk(finish="stop"), USAGE))
    assert c == "ok" and s.content_len == 2 and s.credits() == Decimal("0.0000271")


def test_reasoning_only_length_is_cap_hit_empty():
    # Melious glm-5.3-flash pattern: length, then a trailing chunk with finish_reason null.
    s, c = run(sse(chunk({"reasoning_content": "thinking..."}), chunk(finish="length"),
                   chunk(finish=None), USAGE))
    assert c == "cap_hit_empty" and s.finish == "length" and s.reasoning_len > 0


def test_length_with_content_is_cap_hit():
    _, c = run(sse(chunk({"content": "## Step 1:"}), chunk(finish="length"), USAGE))
    assert c == "cap_hit"


def test_tool_calls_ok():
    tc = {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "read", "arguments": ""}}]}
    tc2 = {"tool_calls": [{"index": 0, "function": {"arguments": "{\"path\":\"x\"}"}}]}
    s, c = run(sse(chunk(tc), chunk(tc2), chunk(finish="tool_calls"), USAGE))
    assert c == "ok" and s.tool_calls == 1


def test_truncated_stream():
    _, c = run(sse(chunk({"content": "partial"}), done=False))
    assert c == "truncated"


def test_http_statuses():
    s = p.StreamSummary()
    assert s.classify(429, True) == "rate_limited"
    assert s.classify(500, True) == "http_error"


def test_non_stream_json():
    s = p.StreamSummary()
    s.from_json(json.dumps({"model": "m", "choices": [{"message": {"content": "", "reasoning_content": "r"},
                                                       "finish_reason": "length"}],
                            "billing_cost": {"credits": "0.1"}}).encode())
    assert s.classify(200, False) == "cap_hit_empty" and s.credits() == Decimal("0.1")


def test_clamp_tokens():
    b = {}
    assert p.clamp_tokens(b) and b["max_tokens"] == p.MAX_TOKENS
    b = {"max_tokens": 10 ** 6}
    p.clamp_tokens(b)
    assert b["max_tokens"] == p.MAX_TOKENS
    b = {"max_completion_tokens": 64}
    assert not p.clamp_tokens(b) and b == {"max_completion_tokens": 64}


def test_refusal_caps():
    run_id = "t1"
    (Path(p.RUNS) / run_id).mkdir(parents=True, exist_ok=True)
    p.total = Decimal(0)
    p.run_spend.clear()
    assert p.refusal(run_id) is None
    (Path(p.RUNS) / run_id / "max_calls").write_text("1")
    p.run_calls[run_id] = 1
    assert "turn" in p.refusal(run_id)
    p.run_calls[run_id] = 0
    p.total = p.CAP
    assert "no new runs" in p.refusal(run_id)          # never spent -> refused
    p.run_spend[run_id] = Decimal("0.5")
    assert p.refusal(run_id) is None                   # in flight -> may finish
    p.total = p.HARD_CAP
    assert "global spend cap" in p.refusal(run_id)
