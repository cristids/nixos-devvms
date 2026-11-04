"""omp-task --best-of N: several independent omp attempts, the wrapper judges.

Each attempt runs the normal sandboxed omp-task in its own copy of a snapshot of
the project (working tree incl. uncommitted and untracked, non-ignored files).
The wrapper runs the success criterion on each copy (in the same sandbox, via
`omp-task --exec`), and the passing attempt with the smallest diff (then fewest
files, then lowest cost) is applied to the original tree, uncommitted, but only
if that tree did not change since the snapshot. Patches and logs stay in the
run dir (~/scratch/.omp-bon/<run-id>); attempt copies are removed afterwards.
"""
import argparse
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OMP_TASK = os.environ.get("OMP_TASK_BIN", "/run/current-system/sw/bin/omp-task")
HOME = Path("/home/cristian")
RUNS = HOME / "scratch" / ".omp-bon"
KEEP_DAYS = 7
# Never part of an attempt's diff (omp runs the tests itself, even if the repo
# has no .gitignore): written to each copy's .git/info/exclude.
CACHE_EXCLUDES = ["__pycache__/", "*.py[co]", ".pytest_cache/", ".mypy_cache/",
                  ".ruff_cache/", ".hypothesis/", "node_modules/", ".venv/"]
# ... and left out of the snapshot (untracked ones; tracked files stay tracked).
CACHE_PATHSPECS = [":(exclude,glob)**/" + (p + "**" if p.endswith("/") else p) for p in CACHE_EXCLUDES]


def die(msg, code=2):
    print(f"omp-task --best-of: {msg}", file=sys.stderr)
    sys.exit(code)


def git(cwd, *args, env=None, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                       env={**os.environ, **(env or {})})
    if check and r.returncode != 0:
        die(f"git {' '.join(args)} failed in {cwd}: {r.stderr.strip()}")
    return r.stdout.strip()


def snapshot_tree(repo):
    """Tree of the working tree (tracked + untracked non-ignored), via a temp index:
    the repo's own index and working tree are not touched."""
    with tempfile.TemporaryDirectory() as td:
        env = {"GIT_INDEX_FILE": os.path.join(td, "index")}
        if git(repo, "rev-parse", "-q", "--verify", "HEAD", check=False):
            git(repo, "read-tree", "HEAD", env=env)
        git(repo, "add", "-A", "--", ".", *CACHE_PATHSPECS, env=env)
        return git(repo, "write-tree", env=env)


def brief_field(brief, name):
    m = re.search(rf"^\s*{name}\s*:\s*(.+)$", brief, re.M | re.I)
    return m.group(1).strip() if m else None


def strip_ticks(s):
    s = s.strip()
    if len(s) > 1 and s[0] == s[-1] == "`":
        s = s[1:-1].strip()
    return s


def allowed_patterns(brief):
    v = brief_field(brief, "Allowed files")
    if not v:
        return None
    return [strip_ticks(p).rstrip("/") for p in re.split(r"[,\s]+", v) if strip_ticks(p)]


def is_allowed(path, pats):
    for p in pats:
        if path == p or path.startswith(p + "/") or fnmatch.fnmatch(path, p):
            return True
    return False


def parse_log(log):
    """Final report and cost from a normal omp-task run's output."""
    text = log.read_text(errors="replace") if log.exists() else ""
    report, _, rest = text.partition("── omp-task: spend by model")
    cost = 0.0
    for m in re.finditer(r"^\S+: \d+ turns, ([0-9.eE-]+)$", rest, re.M):
        cost += float(m.group(1))
    return report.strip(), cost


def run_attempt(k, a):
    st = a["state"]
    d = st["dir"] / f"a{k}"
    log = st["dir"] / f"a{k}.log"
    res = {"k": k, "pass": False, "add": 0, "del": 0, "files": [], "cost": 0.0, "note": ""}
    git(st["dir"], "clone", "-q", "--local", "--no-checkout", str(st["repo"]), str(d))
    git(d, "checkout", "-q", "--detach", st["snap"])
    git(d, "config", "user.name", "omp-bon")
    git(d, "config", "user.email", "omp-bon@localhost")
    with open(d / ".git" / "info" / "exclude", "a") as f:
        f.write("\n".join(CACHE_EXCLUDES) + "\n")
    cmd = [OMP_TASK, "--cwd", str(d), "--max-cost", a["cap"], *a["mode"], a["brief"]]
    t0 = time.time()
    with open(log, "w") as f:
        rc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT).returncode
    res["secs"] = int(time.time() - t0)
    res["report"], res["cost"] = parse_log(log)
    notes = [] if rc == 0 else [f"omp exit {rc}" + (" (cap)" if rc == 3 else "")]
    if git(d, "rev-parse", "HEAD") != st["snap"]:
        notes.append("HEAD moved")
        res["dq"] = True
    # Patch BEFORE the check, so check artefacts (caches) are not part of it.
    git(d, "add", "-A", ".")
    patch = git(d, "diff", "--cached", "--binary", st["snap"]) + "\n"
    (st["dir"] / f"a{k}.patch").write_text(patch if patch.strip() else "")
    for line in git(d, "diff", "--cached", "--numstat", st["snap"]).splitlines():
        ad, de, path = line.split("\t", 2)
        res["add"] += int(ad) if ad.isdigit() else 0
        res["del"] += int(de) if de.isdigit() else 0
        res["files"].append(path)
    if not res["files"]:
        notes.append("no changes")
    if a["allowed"] is not None:
        bad = [p for p in res["files"] if not is_allowed(p, a["allowed"])]
        if bad:
            res["dq"] = True
            notes.append("outside Allowed files: " + ", ".join(bad))
    clog = st["dir"] / f"a{k}.check.log"
    with open(clog, "w") as f:
        try:
            crc = subprocess.run(
                ["timeout", "-k", "10", str(a["timeout"]), OMP_TASK, "--cwd", str(d),
                 "--exec", "bash", "-c", a["check"]],
                stdout=f, stderr=subprocess.STDOUT).returncode
        except OSError as e:
            crc = 127
            f.write(str(e))
    if crc == 124:
        notes.append(f"check timed out ({a['timeout']}s)")
    res["pass"] = crc == 0 and not res.get("dq")
    res["check_rc"] = crc
    res["note"] = "; ".join(notes)
    return res


def prune():
    if not RUNS.is_dir():
        return
    cutoff = time.time() - KEEP_DAYS * 86400
    for p in RUNS.iterdir():
        try:
            if p.is_dir() and p.stat().st_mtime < cutoff:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser(prog="omp-task --best-of", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--best-of", type=int, required=True, metavar="N", help="attempts, 2..5")
    ap.add_argument("--check", help="success criterion command (default: the brief's 'Success criterion:' line)")
    ap.add_argument("--parallel", type=int, metavar="P", help="concurrent attempts (default min(N,2), max 3)")
    ap.add_argument("--check-timeout", type=int, default=600, metavar="S")
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--model")
    ap.add_argument("--max-cost", help="per-attempt cap (USD)")
    ap.add_argument("brief", nargs="+")
    a = ap.parse_args()

    n = a.best_of
    if not 2 <= n <= 5:
        die("--best-of N must be 2..5")
    p = a.parallel or min(n, 2)
    if not 1 <= p <= 3:
        die("--parallel must be 1..3 (memory: each attempt may use 6G)")
    p = min(p, n)
    brief = " ".join(a.brief)
    check = a.check or brief_field(brief, "Success criterion")
    if not check:
        die("needs --check CMD or a 'Success criterion:' line in the brief")
    check = strip_ticks(check)
    cap = a.max_cost or ("0.50" if a.quick else "1.00")
    try:
        capf = float(cap)
    except ValueError:
        die("--max-cost must be a number (USD)")

    repo = Path(a.cwd).resolve()
    if not str(repo).startswith((str(HOME / "development") + "/", str(HOME / "scratch") + "/")) \
            or str(repo).startswith(str(RUNS)):
        die(f"{repo} must be a project directory inside ~/development or ~/scratch")
    top = git(repo, "rev-parse", "--show-toplevel", check=False)
    if not top or Path(top).resolve() != repo:
        die(f"{repo} must be the top of a git repository")

    prune()
    run_id = time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
    rdir = RUNS / run_id
    rdir.mkdir(parents=True)

    head = git(repo, "rev-parse", "-q", "--verify", "HEAD", check=False)
    tree = snapshot_tree(repo)
    ident = {"GIT_AUTHOR_NAME": "omp-bon", "GIT_AUTHOR_EMAIL": "omp-bon@localhost",
             "GIT_COMMITTER_NAME": "omp-bon", "GIT_COMMITTER_EMAIL": "omp-bon@localhost"}
    snap = git(repo, "commit-tree", tree, *(["-p", head] if head else []), "-m", "omp-bon snapshot", env=ident)
    ref = f"refs/omp-bon/{run_id}"
    git(repo, "update-ref", ref, snap)

    mode = ["--quick"] if a.quick else []
    if a.model:
        mode += ["--model", a.model]
    st = {"dir": rdir, "repo": repo, "snap": snap}
    job = {"state": st, "cap": cap, "mode": mode, "brief": brief, "check": check,
           "timeout": a.check_timeout, "allowed": allowed_patterns(brief)}
    print(f"── omp-task --best-of {n}: {'quick' if a.quick else 'plan'}, parallel {p}, "
          f"cap {cap} USD/attempt, TOTAL CAP {n * capf:.2f} USD ──")
    print(f"check: {check}   (timeout {a.check_timeout}s)")
    print(f"allowed files: {', '.join(job['allowed']) if job['allowed'] else '(no Allowed files line: not enforced)'}")
    print(f"run dir: {rdir}", flush=True)

    results = []
    try:
        with ThreadPoolExecutor(max_workers=p) as ex:
            futs = [ex.submit(run_attempt, k, job) for k in range(1, n + 1)]
            for fu in futs:
                try:
                    results.append(fu.result())
                except SystemExit as e:
                    results.append({"k": len(results) + 1, "pass": False, "add": 0, "del": 0,
                                    "files": [], "cost": 0.0, "note": f"setup failed ({e.code})"})
    finally:
        git(repo, "update-ref", "-d", ref, check=False)
        for k in range(1, n + 1):
            shutil.rmtree(rdir / f"a{k}", ignore_errors=True)

    ok = [r for r in results if r["pass"]]
    win = min(ok, key=lambda r: (r["add"] + r["del"], len(r["files"]), r["cost"])) if ok else None
    total = sum(r["cost"] for r in results)

    rows = [("attempt", "pass", "+/-", "files", "cost", "note")]
    for r in results:
        rows.append((f"a{r['k']}" + (" *" if r is win else ""), "yes" if r["pass"] else "no",
                     f"+{r['add']}/-{r['del']}", str(len(r["files"])), f"{r['cost']:.4f}", r["note"]))
    w = [max(len(row[i]) for row in rows) for i in range(5)]
    table = "\n".join("  ".join(c.ljust(w[i]) if i < 5 else c for i, c in enumerate(row)).rstrip() for row in rows)
    print("\n── attempts ──\n" + table)
    (rdir / "summary.txt").write_text(table + f"\ntotal cost {total:.4f} USD\n")

    rc = 0
    if not win:
        print(f"\nNo attempt passed the check: nothing applied. Patches and logs: {rdir}")
        rc = 1
    else:
        wpatch = rdir / f"a{win['k']}.patch"
        print(f"\n── winner a{win['k']}: omp's report ──\n{win['report']}")
        if not win["files"]:
            print("\nWinner has no changes (check already passes): nothing to apply.")
        elif git(repo, "rev-parse", "-q", "--verify", "HEAD", check=False) != head or snapshot_tree(repo) != tree:
            print(f"\nThe tree in {repo} changed since the snapshot: NOT applied.\nPatch: {wpatch}")
            rc = 6
        else:
            r = subprocess.run(["git", "-C", str(repo), "apply", "--binary", str(wpatch)],
                               capture_output=True, text=True)
            if r.returncode != 0:
                print(f"\ngit apply failed ({r.stderr.strip()}): NOT applied.\nPatch: {wpatch}")
                rc = 6
            else:
                print(f"\n── applied a{win['k']} (uncommitted) — git diff --stat ──")
                print(git(repo, "diff", "--stat") or "(only new files)")
                new = git(repo, "ls-files", "--others", "--exclude-standard")
                if new:
                    print("── untracked files ──\n" + new)
    print(f"\n── total cost {total:.4f} USD (cap was {n * capf:.2f}); patches + logs: {rdir} ──")
    sys.exit(rc)


if __name__ == "__main__":
    main()
