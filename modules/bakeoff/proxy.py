"""bakeoff-proxy: recording, spend-capped reverse proxy for the open-weight bake-off.

Successor of ~/development/harness/capture-server.py (a stub that only logged request
bodies); this one forwards, records request + raw response, and accounts spend.

Routes (127.0.0.1 only):
  /r/<run>/melious/v1/...   -> https://api.melious.ai/v1/...  (key injected here; agents never see it)
  /r/<run>/homelab/v1/...   -> http://192.168.40.28:9000/v1/... (local Qwen Flash, ONE stream at a time)
  /r/<run>/webmcp/mcp       -> http://192.168.40.28:9542/mcp     (SearXNG web MCP gateway, token injected)
  GET  /spend               -> {"total": credits, "cap": cap, "runs": {...}}
  POST /admit/<run>         -> 200 if a new run may start, 402 once the global cap is reached

<run> must be an existing directory $BAKEOFF_ROOT/runs/<run> (created by bakeoff-pi/bakeoff-dsh),
so stray clients cannot spend. Per run: runs/<run>/proxy.jsonl (one summary line per call:
model, status, attempts, finish_reason, usage, credits, outcome class) and runs/<run>/raw.jsonl
(full request body + raw response text). Global: ledger/requests.jsonl (append-only spend
ledger; the cumulative total is rebuilt from it at start).

Limits:
  - max output tokens: missing -> 32768, larger -> clamped to 32768 (BAKEOFF_MAX_TOKENS).
  - global Melious spend cap (billing_cost.credits, BAKEOFF_SPEND_CAP, default 200): past it, runs
    that have not spent yet are refused (402); runs in flight may finish up to cap * 1.05.
  - optional per-run caps: runs/<run>/max_credits (credits) and runs/<run>/max_calls (LLM calls =
    turns) -> 402 once reached.
  - Melious 429/502/503/504 before any byte is streamed: retried with backoff (5,10,20,40,60 s).

Outcome classes per call (field "class"):
  ok                 finish_reason stop / tool_calls
  cap_hit            finish_reason length with some content or tool calls
  cap_hit_empty      finish_reason length, empty content and no tool calls (reasoning-only turn)
  truncated          stream ended without a finish_reason ([DONE] missing or not) -> infra-fail
  rate_limited       final status 429 after retries
  http_error         other status >= 400
  refused            refused by this proxy (spend cap / per-run budget)
"""

import asyncio
import json
import os
import re
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, TCPConnector, web

ROOT = Path(os.environ.get("BAKEOFF_ROOT", "/home/cristian/bakeoff"))
RUNS = ROOT / "runs"
LEDGER = ROOT / "ledger" / "requests.jsonl"
CAP = Decimal(os.environ.get("BAKEOFF_SPEND_CAP", "200"))
HARD_CAP = CAP * Decimal("1.05")
MAX_TOKENS = int(os.environ.get("BAKEOFF_MAX_TOKENS", "32768"))
LISTEN = os.environ.get("BAKEOFF_PROXY_LISTEN", "127.0.0.1:5099")
CRED_DIR = Path(os.environ.get("CREDENTIALS_DIRECTORY", "/nonexistent"))

UPSTREAMS = {
    "melious": "https://api.melious.ai",
    "homelab": os.environ.get("BAKEOFF_HOMELAB_URL", "http://192.168.40.28:9000"),
    "webmcp": os.environ.get("BAKEOFF_WEBMCP_URL", "http://192.168.40.28:9542"),
}
# Per-model sampling defaults from the official cards (set by the Nix module). Applied only to
# keys the client did not send, so pi (which sends them) and dsh (which does not) run the
# same sampling.
SAMPLING = json.loads(os.environ.get("BAKEOFF_SAMPLING", "{}"))
RETRY_STATUS = {429, 502, 503, 504}
BACKOFF = [5, 10, 20, 40, 60]
RAW_LIMIT = 32 * 1024 * 1024
RUN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
HOP = {"host", "content-length", "transfer-encoding", "connection", "keep-alive",
       "authorization", "accept-encoding", "te", "trailer", "upgrade", "proxy-authorization"}


def _secret(name: str) -> str:
    p = CRED_DIR / name
    return p.read_text().strip() if p.is_file() else ""


MELIOUS_KEY = _secret("melious")
WEBMCP_TOKEN = _secret("webmcp")

total = Decimal(0)
run_spend: dict[str, Decimal] = {}
run_calls: dict[str, int] = {}
homelab_lock = asyncio.Lock()
ledger_lock = asyncio.Lock()


def load_ledger() -> None:
    global total
    if not LEDGER.exists():
        return
    for line in LEDGER.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        c = Decimal(str(r.get("credits") or 0))
        total += c
        run_spend[r["run"]] = run_spend.get(r["run"], Decimal(0)) + c


def append(path: Path, obj: dict) -> None:
    with open(path, "a") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")


def run_budget(run: str) -> Decimal | None:
    p = RUNS / run / "max_credits"
    try:
        return Decimal(p.read_text().strip())
    except Exception:
        return None


def refusal(run: str) -> str | None:
    spent = run_spend.get(run)
    if total >= HARD_CAP:
        return f"global spend cap reached ({total} >= {HARD_CAP})"
    if total >= CAP and spent is None:
        return f"global spend cap reached ({total} >= {CAP}); no new runs"
    b = run_budget(run)
    if b is not None and (spent or 0) >= b:
        return f"run budget reached ({spent} >= {b})"
    try:
        mc = int((RUNS / run / "max_calls").read_text().strip())
    except Exception:
        mc = None
    if mc is not None and run_calls.get(run, 0) >= mc:
        return f"run call (turn) cap reached ({run_calls.get(run, 0)} >= {mc})"
    return None


def clamp_tokens(body: dict) -> dict:
    """Explicit output cap on every chat/completions request. Returns what was changed."""
    changed = {}
    keys = [k for k in ("max_tokens", "max_completion_tokens") if k in body]
    if not keys:
        body["max_tokens"] = MAX_TOKENS
        changed["max_tokens"] = ["missing", MAX_TOKENS]
    for k in keys:
        if not isinstance(body[k], int) or body[k] > MAX_TOKENS:
            changed[k] = [body[k], MAX_TOKENS]
            body[k] = MAX_TOKENS
    return changed


class StreamSummary:
    """Accumulates an OpenAI chat stream (SSE) or a JSON response into a call summary."""

    def __init__(self) -> None:
        self.buf = b""
        self.finish = None
        self.content_len = 0
        self.reasoning_len = 0
        self.tool_calls = 0
        self.usage = None
        self.billing = None
        self.done = False
        self.model = None

    def _choice(self, ch: dict, delta_key: str) -> None:
        d = ch.get(delta_key) or {}
        self.content_len += len(d.get("content") or "")
        self.reasoning_len += len(d.get("reasoning_content") or d.get("reasoning") or "")
        tcs = d.get("tool_calls") or []
        if delta_key == "message":
            self.tool_calls += len(tcs)
        else:
            self.tool_calls += sum(1 for t in tcs if (t.get("function") or {}).get("name") or t.get("id"))
        if ch.get("finish_reason"):
            self.finish = ch["finish_reason"]   # later null chunks never reset it

    def _obj(self, o: dict, delta_key: str) -> None:
        self.model = o.get("model") or self.model
        for ch in o.get("choices") or []:
            self._choice(ch, delta_key)
        if o.get("usage"):
            self.usage = o["usage"]
        if o.get("billing_cost"):
            self.billing = o["billing_cost"]

    def feed(self, chunk: bytes) -> None:
        self.buf += chunk
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                self.done = True
                continue
            try:
                self._obj(json.loads(data), "delta")
            except ValueError:
                pass

    def from_json(self, raw: bytes) -> None:
        try:
            self._obj(json.loads(raw), "message")
            self.done = True
        except ValueError:
            pass

    def credits(self) -> Decimal:
        try:
            return Decimal(str((self.billing or {}).get("credits") or 0))
        except Exception:
            return Decimal(0)

    def classify(self, status: int, stream: bool) -> str:
        if status == 429:
            return "rate_limited"
        if status >= 400:
            return "http_error"
        if self.finish is None:
            return "truncated"
        if self.finish == "length":
            return "cap_hit" if (self.content_len or self.tool_calls) else "cap_hit_empty"
        return "ok"


async def handle_spend(request: web.Request) -> web.Response:
    return web.json_response({"total": str(total), "cap": str(CAP), "hard_cap": str(HARD_CAP),
                              "runs": {k: str(v) for k, v in sorted(run_spend.items())}})


async def handle_admit(request: web.Request) -> web.Response:
    run = request.match_info["run"]
    if not RUN_RE.match(run) or not (RUNS / run).is_dir():
        return web.json_response({"error": "unknown run"}, status=404)
    if total >= CAP:
        return web.json_response({"error": f"global spend cap reached ({total} >= {CAP})",
                                  "total": str(total)}, status=402)
    return web.json_response({"ok": True, "total": str(total), "cap": str(CAP)})


def _err(status: int, msg: str, kind: str) -> web.Response:
    return web.json_response({"error": {"message": msg, "type": kind}}, status=status)


async def handle_proxy(request: web.Request) -> web.StreamResponse:
    global total
    run, up, tail = request.match_info["run"], request.match_info["up"], request.match_info["tail"]
    rundir = RUNS / run
    if not RUN_RE.match(run) or not rundir.is_dir():
        return _err(404, f"unknown run {run!r} (create {rundir} first)", "bakeoff_unknown_run")
    if up not in UPSTREAMS:
        return _err(404, f"unknown upstream {up!r}", "bakeoff_unknown_upstream")
    req_id = uuid.uuid4().hex[:12]
    t0 = time.time()
    body = await request.read()
    url = UPSTREAMS[up] + "/" + tail
    if request.query_string:
        url += "?" + request.query_string
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
    rec: dict = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "id": req_id, "run": run,
                 "upstream": up, "method": request.method, "path": "/" + tail}

    if up == "webmcp":
        if WEBMCP_TOKEN:
            headers["Authorization"] = f"Bearer {WEBMCP_TOKEN}"
        try:
            j = json.loads(body) if body else None
            if isinstance(j, dict):
                rec["rpc"] = {"method": j.get("method"), "params": j.get("params")}
        except ValueError:
            pass
        return await _forward(request, url, headers, body, rec, rundir, summary=None)

    # LLM upstreams: chat/completions bodies get the output cap; spend/turn checks first.
    is_chat = request.method == "POST" and tail.endswith("chat/completions")
    if is_chat:
        why = refusal(run)
        if why:
            rec.update(status=402, **{"class": "refused"}, reason=why)
            append(rundir / "proxy.jsonl", rec)
            return _err(402, why, "bakeoff_spend_cap")
        run_calls[run] = run_calls.get(run, 0) + 1
    if up == "melious":
        if not MELIOUS_KEY:
            return _err(500, "proxy has no Melious key (LoadCredential melious)", "bakeoff_no_key")
        headers["Authorization"] = f"Bearer {MELIOUS_KEY}"
    stream = False
    if request.method == "POST" and body:
        try:
            j = json.loads(body)
        except ValueError:
            j = None
        if isinstance(j, dict) and tail.endswith("chat/completions"):
            changed = clamp_tokens(j)
            if changed:
                rec["clamped"] = changed
            added = {k: v for k, v in SAMPLING.get(j.get("model") or "", {}).items() if k not in j}
            if added:
                j.update(added)
                rec["sampling_defaults"] = added
            stream = bool(j.get("stream"))
            rec["model"] = j.get("model")
            rec["max_tokens"] = j.get("max_tokens", j.get("max_completion_tokens"))
            rec["sampling"] = {k: j[k] for k in ("temperature", "top_p", "top_k", "min_p",
                               "presence_penalty", "reasoning_effort") if k in j}
            body = json.dumps(j).encode()
    rec["stream"] = stream
    summary = StreamSummary()
    if up == "homelab":
        async with homelab_lock:   # llama-swap peer serves one stream; serialize our lanes
            rec["queued_s"] = round(time.time() - t0, 2)
            return await _forward(request, url, headers, body, rec, rundir, summary)
    return await _forward(request, url, headers, body, rec, rundir, summary)


async def _forward(request, url, headers, body, rec, rundir, summary):
    global total
    t0 = time.time()
    sess: ClientSession = request.app["session"]
    attempts = 0
    resp = None
    while True:
        attempts += 1
        try:
            resp = await sess.request(request.method, url, headers=headers, data=body or None)
        except Exception as exc:  # connection errors: retry like 5xx
            if attempts <= len(BACKOFF) and rec["upstream"] == "melious":
                rec.setdefault("retries", []).append(f"{type(exc).__name__}: {exc}"[:200])
                await asyncio.sleep(BACKOFF[attempts - 1])
                continue
            rec.update(status=599, attempts=attempts, error=str(exc)[:500],
                       latency_s=round(time.time() - t0, 2), **{"class": "http_error"})
            append(rundir / "proxy.jsonl", rec)
            return _err(502, f"upstream error: {exc}", "bakeoff_upstream_error")
        if (resp.status in RETRY_STATUS and rec["upstream"] == "melious"
                and attempts <= len(BACKOFF)):
            txt = (await resp.read())[:300].decode("utf-8", "replace")
            resp.release()
            rec.setdefault("retries", []).append(f"{resp.status}: {txt}")
            await asyncio.sleep(BACKOFF[attempts - 1])
            continue
        break

    out = web.StreamResponse(status=resp.status)
    for k, v in resp.headers.items():
        if k.lower() not in HOP and k.lower() != "content-encoding":
            out.headers[k] = v
    await out.prepare(request)
    raw = bytearray()
    is_sse = "text/event-stream" in resp.headers.get("Content-Type", "")
    client_gone = False
    try:
        async for chunk in resp.content.iter_any():
            if len(raw) < RAW_LIMIT:
                raw += chunk
            if summary is not None and is_sse:
                summary.feed(chunk)
            if not client_gone:
                try:
                    await out.write(chunk)
                except (ConnectionResetError, RuntimeError):
                    client_gone = True
    except Exception as exc:
        rec["stream_error"] = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        resp.release()
    if summary is not None and not is_sse:
        summary.from_json(bytes(raw))
    rec.update(status=resp.status, attempts=attempts, latency_s=round(time.time() - t0, 2))
    if client_gone:
        rec["client_disconnected"] = True
    if summary is not None and rec.get("path", "").endswith("chat/completions"):
        credits = summary.credits()
        rec.update({"class": summary.classify(resp.status, rec.get("stream", False)),
                    "finish_reason": summary.finish, "done_marker": summary.done,
                    "content_len": summary.content_len, "reasoning_len": summary.reasoning_len,
                    "tool_calls": summary.tool_calls, "usage": summary.usage,
                    "credits": str(credits), "model_resp": summary.model})
        if credits:
            async with ledger_lock:
                total += credits
                run_spend[rec["run"]] = run_spend.get(rec["run"], Decimal(0)) + credits
                append(LEDGER, {"ts": rec["ts"], "run": rec["run"], "id": rec["id"],
                                "model": rec.get("model"), "credits": str(credits)})
        elif rec["upstream"] == "melious":
            run_spend.setdefault(rec["run"], Decimal(0))
    elif summary is None:
        rec["response_head"] = bytes(raw[:4000]).decode("utf-8", "replace")
    append(rundir / "proxy.jsonl", rec)
    append(rundir / "raw.jsonl", {"id": rec["id"], "request": body.decode("utf-8", "replace") if body else "",
                                  "response": bytes(raw).decode("utf-8", "replace")})
    try:
        await out.write_eof()
    except Exception:
        pass
    return out


async def on_startup(app: web.Application) -> None:
    app["session"] = ClientSession(timeout=ClientTimeout(total=None, sock_connect=30, sock_read=900),
                                   connector=TCPConnector(limit=64), auto_decompress=True)


async def on_cleanup(app: web.Application) -> None:
    await app["session"].close()


def main() -> None:
    RUNS.mkdir(parents=True, exist_ok=True)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    load_ledger()
    print(f"bakeoff-proxy: spend so far {total} / cap {CAP}; melious key {'loaded' if MELIOUS_KEY else 'MISSING'}; "
          f"webmcp token {'loaded' if WEBMCP_TOKEN else 'MISSING'}", file=sys.stderr, flush=True)
    app = web.Application(client_max_size=256 * 1024 * 1024)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/spend", handle_spend)
    app.router.add_post("/admit/{run}", handle_admit)
    app.router.add_route("*", "/r/{run}/{up}/{tail:.*}", handle_proxy)
    host, port = LISTEN.rsplit(":", 1)
    web.run_app(app, host=host, port=int(port), access_log=None, print=None)


if __name__ == "__main__":
    main()
