#!/usr/bin/env python3
"""pi-task cost before/after report from /var/lib/pi-task-control/audit.jsonl.

  pi-task-cost-report --since 2026-11-08 --until 2026-11-12        # one window (days inclusive)
  pi-task-cost-report --compare 2026-11-08:2026-11-12 2026-11-14:2026-11-20

Credits = sum of `settle` charged (Melious credits the control proxy settled); a
worker-request = one settled provider request; a worker run = one `register`.
Per-day rows show charged, requests, runs, finished runs by outcome.
The lean-coding default-on deploy time is read from LEAN_MARK (written at deploy).
"""
import argparse
import collections
import datetime as dt
import json
from decimal import Decimal

AUDIT = "/var/lib/pi-task-control/audit.jsonl"
LEAN_MARK = "/home/cristian/.local/state/pi-task-lean-deployed"  # one line: ISO timestamp of the deploy


def day(ts: str) -> str:
    return ts[:10]


def load(path: str):
    with open(path) as f:
        for ln in f:
            try:
                yield json.loads(ln)
            except ValueError:
                continue


def window(path: str, since: str, until: str) -> dict:
    per = collections.defaultdict(lambda: {"charged": Decimal(0), "requests": 0, "runs": 0, "finished": collections.Counter()})
    for e in load(path):
        d = day(str(e.get("timestamp", "")))
        if not (since <= d <= until):
            continue
        a = e.get("action")
        if a == "settle" and (e.get("new") or {}).get("status") == "ok":
            per[d]["charged"] += Decimal(str((e.get("new") or {}).get("charged", "0")))
            per[d]["requests"] += 1
        elif a == "register":
            per[d]["runs"] += 1
        elif a == "finish":
            per[d]["finished"][(e.get("new") or {}).get("outcome", "?")] += 1
    days = sorted(per)
    tot_c = sum((per[d]["charged"] for d in days), Decimal(0))
    tot_r = sum(per[d]["requests"] for d in days)
    tot_w = sum(per[d]["runs"] for d in days)
    n = len(days) or 1
    return {"since": since, "until": until, "days": days, "per": per, "charged": tot_c, "requests": tot_r, "runs": tot_w,
            "per_day": tot_c / n, "per_request": (tot_c / tot_r) if tot_r else Decimal(0),
            "per_run": (tot_c / tot_w) if tot_w else Decimal(0)}


def show(w: dict) -> None:
    print(f"window {w['since']} .. {w['until']} ({len(w['days'])} day(s) with activity)")
    print(f"{'day':<11} {'credits':>10} {'requests':>9} {'runs':>5}  finished")
    for d in w["days"]:
        p = w["per"][d]
        print(f"{d:<11} {p['charged']:>10.3f} {p['requests']:>9} {p['runs']:>5}  {dict(p['finished'])}")
    print(f"total credits {w['charged']:.3f}; per day {w['per_day']:.3f}; per worker-request {w['per_request']:.5f}; "
          f"per worker run {w['per_run']:.4f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--audit", default=AUDIT)
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--compare", nargs=2, metavar="FROM:TO")
    a = ap.parse_args()
    try:
        mark = open(LEAN_MARK).read().strip()
    except OSError:
        mark = "unknown (no " + LEAN_MARK + ")"
    print(f"lean-coding default-on for pi-task workers deployed: {mark}")
    if a.compare:
        ws = [window(a.audit, *x.split(":")) for x in a.compare]
        for w in ws:
            show(w)
            print()
        b, c = ws
        pct = lambda x, y: f"{(y / x - 1) * 100:+.0f}%" if x else "n/a"
        print(f"after vs before: credits/day {pct(b['per_day'], c['per_day'])}, per worker-request "
              f"{pct(b['per_request'], c['per_request'])}, per worker run {pct(b['per_run'], c['per_run'])}")
    else:
        today = dt.date.today().isoformat()
        show(window(a.audit, a.since or "2000-01-01", a.until or today))


if __name__ == "__main__":
    main()
