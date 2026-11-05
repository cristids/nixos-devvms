"""Drive one taskplane batch headlessly through `pi --mode rpc` (bakeoff-pi --orch).

`pi -p "/orch all"` cannot work: print mode finalizes the session as soon as the slash command
returns, and taskplane's supervisor then crashes on the stale extension context. RPC mode keeps
the session alive; this driver sends the prompt, mirrors pi's stdout (JSON events) to its own
stdout, and ends the session once the batch is terminal:
  .pi/batch-state.json phase in complete/error/aborted, or the file vanished after it existed
  and .pi/batch-history.json gained an entry; or pi exits; or the wall-clock limit (caller's
  `timeout`). A batch that never starts within START_GRACE seconds also ends the run.

usage: orch-driver.py <repo> <prompt> -- <pi argv...>
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

START_GRACE = int(os.environ.get("BAKEOFF_ORCH_START_GRACE", "900"))
SETTLE = 20  # seconds to let the supervisor finish its epilogue after a terminal phase


def main() -> int:
    repo, prompt = Path(sys.argv[1]), sys.argv[2]
    argv = sys.argv[sys.argv.index("--") + 1:]
    state, hist = repo / ".pi" / "batch-state.json", repo / ".pi" / "batch-history.json"

    def hist_len() -> int:
        try:
            return len(json.loads(hist.read_text()))
        except Exception:
            return 0

    h0 = hist_len()
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def pump() -> None:
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    proc.stdin.write(json.dumps({"id": "orch-1", "type": "prompt", "message": prompt}) + "\n")
    proc.stdin.flush()

    t0, seen, phase, why = time.time(), False, None, "pi exited"
    while proc.poll() is None:
        time.sleep(5)
        if state.exists():
            seen = True
            try:
                phase = json.loads(state.read_text()).get("phase")
            except Exception:
                continue
            if phase in ("complete", "error", "aborted"):
                why = f"batch phase {phase}"
                break
        elif seen and hist_len() > h0:
            why = "batch state removed, history appended"
            break
        if not seen and hist_len() > h0:
            why = "batch finished (history appended)"
            break
        if not seen and time.time() - t0 > START_GRACE:
            why = f"no batch started within {START_GRACE}s"
            break
    if proc.poll() is None:
        time.sleep(SETTLE)
        try:
            proc.stdin.write(json.dumps({"id": "bye", "type": "abort"}) + "\n")
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
    t.join(timeout=5)
    print(json.dumps({"type": "bakeoff_orch_end", "reason": why, "phase": phase,
                      "history_entries": hist_len() - h0, "secs": round(time.time() - t0)}), flush=True)
    return 0 if (phase == "complete" or hist_len() > h0) else 1


if __name__ == "__main__":
    sys.exit(main())
