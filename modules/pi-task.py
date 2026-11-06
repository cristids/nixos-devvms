#!/usr/bin/env python3
"""Bounded production runner for Pi on devpro."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import stat
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


EXIT_POLICY = 6
HOME = Path("/home/cristian")
MODEL_SPECS = {
    "local-qwen": {
        "pi_model": "homelab/strata/qwen3.8-flash",
        "event_model": "strata/qwen3.8-flash",
    },
    "melious-dsf": {
        "pi_model": "melious/deepseek-v4.1-flash-1017",
        "event_model": "deepseek-v4.1-flash-1017",
    },
}
ALLOWED_BENCHES = {"test", *{f"test{i}" for i in range(1, 8)}}
W_ROOT = Path("/srv/pi-envs")
W_SLOTS = {f"w{i}" for i in range(1, 7)}
ALLOWED_BENCHES |= W_SLOTS
SEARXNG_HEALTH = "http://192.168.40.28:8080/healthz"
PI_TASK_CONTROL = "/run/current-system/sw/bin/pi-task-control"
WORKER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
INET_RE = re.compile(
    r"sa_family=AF_INET6?.*?sin6?_port=htons\((\d+)\).*?"
    r"(?:inet_addr\(|inet_pton\(AF_INET6, )\"([^\"]+)\"")
UNIX_RE = re.compile(r"sa_family=AF_UNIX.*?sun_path=\"([^\"]+)\"")


class PolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitLayout:
    dotgit: Path
    gitdir: Path
    common: Path


class AuditLogger:
    """Append only non-content run telemetry for reviewer supervision."""

    def __init__(self, run_id: str, provider: str, worker_id: str):
        state = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / "pi-task"
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(state, 0o700)
        self.path = state / "audit.jsonl"
        self.context = {"run_id": run_id, "provider": provider, "worker_id": worker_id}
        self.tool_started: dict[str, float] = {}

    def write(self, kind: str, **metadata) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **self.context, "kind": kind, **metadata,
        }
        lock_path = self.path.with_suffix(".lock")
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if self.path.exists() and self.path.stat().st_size >= 10 * 1024 * 1024:
                previous = self.path.with_suffix(".jsonl.1")
                os.replace(self.path, previous)
            with self.path.open("a") as handle:
                os.chmod(self.path, 0o600)
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=False, **kwargs)


def git(repo: Path, *args: str, check: bool = True, text: bool = False):
    result = run(["git", "-C", str(repo), *args], stdout=subprocess.PIPE,
                 stderr=subprocess.PIPE, text=text)
    if check and result.returncode:
        err = result.stderr if text else result.stderr.decode(errors="replace")
        raise PolicyError(f"git {' '.join(args)} failed: {err.strip()}")
    return result.stdout


def within_project_roots(path: Path) -> bool:
    roots = (HOME / "development", HOME / "scratch")
    return any(path.is_relative_to(root) for root in roots) or w_slot(path) is not None


def w_slot(path: Path) -> Path | None:
    """Recognize only exact canonical W1-W6 descendants, never .control/aliases."""
    if not path.is_relative_to(W_ROOT):
        return None
    relative = path.relative_to(W_ROOT)
    if not relative.parts or relative.parts[0] not in W_SLOTS:
        return None
    root = W_ROOT / relative.parts[0]
    if root.resolve() != root or path.resolve() != path:
        raise PolicyError("W paths must be canonical and cannot escape through symlinks")
    return root


def validate_w_assignment(repo: Path, layout: GitLayout, bench: str | None) -> Path | None:
    root = w_slot(repo)
    if root is None:
        if bench in W_SLOTS:
            raise PolicyError("W work must use its own checkout below /srv/pi-envs/wN")
        return None
    if bench != root.name:
        raise PolicyError("--erpnext-bench must match the checkout's W slot")
    if repo == root or not layout.common.is_relative_to(root) or not layout.gitdir.is_relative_to(root):
        raise PolicyError("W checkout and all Git metadata must belong to the assigned slot")
    if not (root / "toolchain.sh").is_file():
        raise PolicyError("W environment is not provisioned: missing toolchain.sh")
    linked = [p.resolve() for p in (root / "apps").iterdir() if p.is_dir()]
    if repo not in linked:
        raise PolicyError("W checkout must be linked as an installed app before running")
    return root


def acquire_w_lease(root: Path):
    path = W_ROOT / ".control/leases" / f"{root.name}.lock"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = path.open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise PolicyError(f"W environment is already allocated: {root.name}") from exc
    return handle


def w_app_snapshots(root: Path, selected: Path) -> dict[Path, dict]:
    result = {}
    for app in (root / "apps").iterdir():
        target = app.resolve()
        if not target.is_relative_to(root):
            raise PolicyError(f"W app escapes its assigned environment: {app.name}")
        if target != selected and (target / ".git").exists():
            result[target] = snapshot(target)
    return result


def w_app_links(root: Path) -> dict[str, str]:
    return {app.name: str(app.resolve()) for app in (root / "apps").iterdir()}


def w_scope_violations(before: dict[Path, dict]) -> list[str]:
    return [f"{app.name}: {problem}" for app, state in before.items()
            for problem in policy_violations(state, snapshot(app), set())]


def git_layout(repo: Path) -> GitLayout:
    dotgit = repo / ".git"
    if not dotgit.exists():
        raise PolicyError(f"missing Git metadata: {dotgit}")
    gitdir = Path(git(repo, "rev-parse", "--absolute-git-dir", text=True).strip()).resolve()
    common_raw = git(repo, "rev-parse", "--git-common-dir", text=True).strip()
    common = Path(common_raw)
    if not common.is_absolute():
        common = repo / common
    common = common.resolve()
    for label, path in (("gitdir", gitdir), ("common gitdir", common)):
        if not path.is_dir() or not within_project_roots(path):
            raise PolicyError(f"{label} must be a directory below ~/development or ~/scratch: {path}")
    return GitLayout(dotgit=dotgit, gitdir=gitdir, common=common)


def git_mount_args(layout: GitLayout) -> list[str]:
    args = ["--ro-bind", str(layout.dotgit), str(layout.dotgit)]
    if layout.common != layout.dotgit:
        args += ["--ro-bind", str(layout.common), str(layout.common)]
    if layout.gitdir != layout.common and not layout.gitdir.is_relative_to(layout.common):
        args += ["--ro-bind", str(layout.gitdir), str(layout.gitdir)]
    return args


def project_mount_args(repo: Path, layout: GitLayout) -> list[str]:
    # Both trusted providers receive the allocated checkout and the same Git
    # context. The worktree is writable, while normal-clone or linked-worktree
    # metadata is rebound read-only so Pi cannot commit or move refs.
    return ["--bind", str(repo), str(repo), *git_mount_args(layout)]


def validate_repo(value: str) -> Path:
    repo = Path(value).expanduser().resolve(strict=True)
    if not repo.is_dir():
        raise PolicyError(f"{repo} is not a directory")
    roots = (HOME / "development", HOME / "scratch")
    if not any(repo.is_relative_to(root) and repo != root for root in roots) and w_slot(repo) is None:
        raise PolicyError(f"{repo} must be below ~/development or ~/scratch")
    top = Path(git(repo, "rev-parse", "--show-toplevel", text=True).strip()).resolve()
    if top != repo:
        raise PolicyError(f"--cwd must be the Git project root ({top})")
    git(repo, "rev-parse", "--verify", "HEAD")
    git_layout(repo)
    return repo


def validate_allowed(repo: Path, values: list[str]) -> set[str]:
    if not values:
        raise PolicyError("at least one --allow FILE is required")
    allowed: set[str] = set()
    for raw in values:
        posix = PurePosixPath(raw)
        if posix.is_absolute() or not posix.parts or ".." in posix.parts:
            raise PolicyError(f"invalid allowed path: {raw!r}")
        normalized = str(posix)
        if normalized in (".", ".git") or normalized.startswith(".git/"):
            raise PolicyError(f"git metadata cannot be allowed: {raw!r}")
        target = repo / normalized
        resolved = target.resolve(strict=False)
        if not resolved.is_relative_to(repo):
            raise PolicyError(f"allowed path escapes the project: {raw!r}")
        if target.exists() and target.is_dir():
            raise PolicyError(f"--allow names files, not directories: {raw!r}")
        allowed.add(normalized)
    return allowed


def digest_path(path: Path) -> str:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "missing"
    if stat.S_ISLNK(info.st_mode):
        return "link:" + os.readlink(path)
    if stat.S_ISREG(info.st_mode):
        h = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(block)
        return f"file:{info.st_mode & 0o7777:o}:{h.hexdigest()}"
    if stat.S_ISDIR(info.st_mode):
        # Directories returned by git ls-files are normally gitlinks. An empty
        # gitlink has no nested .git, so `git -C path` walks upward and would
        # otherwise digest the enclosing worker checkout. Only query Git when
        # the directory itself is a repository; fingerprint plain contents so
        # changes below an unpopulated gitlink still fail closed.
        top = git(path, "rev-parse", "--show-toplevel", check=False,
                  text=True).strip()
        if top and Path(top).resolve() == path.resolve():
            subhead = git(path, "rev-parse", "-q", "--verify", "HEAD", check=False,
                          text=True).strip()
            substatus = git(path, "status", "--porcelain=v1", "--untracked-files=all",
                            check=False)
            return "dir:" + subhead + ":" + hashlib.sha256(substatus).hexdigest()
        h = hashlib.sha256()
        for child in sorted(path.iterdir(), key=lambda item: os.fsencode(item.name)):
            h.update(os.fsencode(child.name))
            h.update(b"\0")
            h.update(digest_path(child).encode(errors="surrogateescape"))
            h.update(b"\0")
        return "dir-tree:" + h.hexdigest()
    return f"other:{info.st_mode}"


def snapshot(repo: Path) -> dict:
    raw = git(repo, "ls-files", "-c", "-o", "--exclude-standard", "-z")
    paths = {item.decode(errors="surrogateescape") for item in raw.split(b"\0") if item}
    return {
        "head": git(repo, "rev-parse", "HEAD", text=True).strip(),
        "index": hashlib.sha256(git(repo, "ls-files", "--stage", "-z")).hexdigest(),
        "files": {name: digest_path(repo / name) for name in sorted(paths)},
    }


def policy_violations(before: dict, after: dict, allowed: set[str]) -> list[str]:
    problems: list[str] = []
    if before["head"] != after["head"]:
        problems.append(f"HEAD moved: {before['head']} -> {after['head']}")
    if before["index"] != after["index"]:
        problems.append("the Git index changed")
    names = set(before["files"]) | set(after["files"])
    for name in sorted(names - allowed):
        if before["files"].get(name, "missing") != after["files"].get(name, "missing"):
            problems.append(f"out-of-scope path changed: {name}")
    return problems


def acquire_exclusive_lock(runtime: Path, name: str):
    path = runtime / f"pi-task-exclusive-{name}.lock"
    handle = path.open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise PolicyError(f"exclusive resource is already allocated: {name}") from exc
    handle.write(f"pid={os.getpid()}\n")
    handle.flush()
    return handle


def check_melious_health() -> None:
    try:
        with urlopen("http://127.0.0.1:5098/health", timeout=45) as response:
            payload = json.load(response)
    except (OSError, URLError, ValueError) as exc:
        raise PolicyError(f"Melious DSF provider unavailable: {exc}") from exc
    expected = MODEL_SPECS["melious-dsf"]["event_model"]
    if payload.get("status") != "ok" or payload.get("model") != expected:
        raise PolicyError(f"Melious DSF model verification failed: expected {expected}")


def check_local_health() -> None:
    expected = MODEL_SPECS["local-qwen"]["event_model"]
    try:
        with urlopen("http://192.168.40.28:9000/v1/models", timeout=10) as response:
            payload = json.load(response)
        ids = {item.get("id") for item in payload.get("data", [])}
    except (OSError, URLError, ValueError, AttributeError) as exc:
        raise PolicyError(f"local Qwen provider unavailable: {exc}") from exc
    if expected not in ids:
        raise PolicyError(f"local Qwen model unavailable: required {expected}")


def check_web_mcp_health() -> tuple[bool, str]:
    try:
        with urlopen(SEARXNG_HEALTH, timeout=5) as response:
            if response.status != 200:
                raise ValueError(f"HTTP {response.status}")
    except (OSError, URLError, ValueError, AttributeError) as exc:
        return False, str(exc)
    return True, "ok"


def web_admission(required: bool) -> tuple[bool, str]:
    available, detail = check_web_mcp_health()
    if required and not available:
        raise PolicyError(f"SearXNG MCP backend unavailable (degraded): {detail}")
    return available, detail


def validate_model_config(path: Path) -> None:
    try:
        config = json.loads(path.read_text())
        providers = config["providers"]
        exposed = {
            f"{provider}/{model['id']}"
            for provider, value in providers.items()
            for model in value.get("models", [])
        }
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PolicyError(f"invalid Pi model config: {path}") from exc
    expected = {value["pi_model"] for value in MODEL_SPECS.values()}
    if exposed != expected or set(providers) != {"homelab", "melious"}:
        raise PolicyError(
            f"Pi model config must expose exactly {sorted(expected)}; got {sorted(exposed)}")


def write_run_models(stage: Path, source: Path, model: str,
                     worker_id: str | None) -> Path:
    validate_model_config(source)
    config = json.loads(source.read_text())
    if model == "melious-dsf":
        if not worker_id:
            raise PolicyError("--worker-id is required for melious-dsf")
        config["providers"]["melious"]["baseUrl"] = (
            f"http://127.0.0.1:5098/w/{worker_id}/v1")
    path = stage / "models.json"
    path.write_text(json.dumps(config, separators=(",", ":")))
    path.chmod(0o600)
    return path


def control_call(*args: str) -> dict:
    result = run([PI_TASK_CONTROL, *args], stdout=subprocess.PIPE,
                 stderr=subprocess.PIPE, text=True)
    if result.returncode:
        raise PolicyError(
            f"DSF control request failed: {(result.stderr or result.stdout).strip()}")
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise PolicyError("DSF control returned invalid status") from exc


def report_pane(state: str, message: str) -> bool:
    """Report from the supervisor: Pi's upstream hook only covers TUI mode.

    Always address our own authenticated pane; never fall back to the focused
    pane. Reporting is bounded and must not change a worker's outcome.
    """
    pane = os.environ.get("TUIOS_PANE_ID")
    session = os.environ.get("TUIOS_SESSION")
    if not pane or not session or not os.environ.get("TUIOS_SOCKET"):
        return False
    try:
        result = subprocess.run([
            "/run/current-system/sw/bin/tuios", "set-agent-state", state,
            "--harness", "pi", "--session", session, "--window", pane,
            "--message", message,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        if result.returncode == 0:
            return True
    except (OSError, subprocess.TimeoutExpired):
        pass
    print("pi-task: WARNING: TUIOS state report unavailable; worker continues",
          file=sys.stderr)
    return False


def accounting(events: Path, friendly_model: str, elapsed: float, pi_rc: int) -> dict:
    expected = MODEL_SPECS[friendly_model]["event_model"]
    totals = {"turns": 0, "input": 0, "output": 0, "cache_read": 0, "cost": 0.0}
    seen_models = set()
    for line in events.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = event.get("message", {})
        if event.get("type") != "message_end" or message.get("role") != "assistant":
            continue
        seen_models.add(message.get("model"))
        usage = message.get("usage") or {}
        totals["turns"] += 1
        totals["input"] += int(usage.get("input") or 0)
        totals["output"] += int(usage.get("output") or 0)
        totals["cache_read"] += int(usage.get("cacheRead") or 0)
        totals["cost"] += float((usage.get("cost") or {}).get("total") or 0)
    if pi_rc == 0 and seen_models != {expected}:
        raise PolicyError(
            f"model identity mismatch: expected only {expected}, observed {sorted(seen_models)}")
    print("── pi-task accounting: "
          f"model={friendly_model}/{expected} turns={totals['turns']} "
          f"input={totals['input']} output={totals['output']} "
          f"cache_read={totals['cache_read']} cost={totals['cost']:.6f} "
          f"elapsed={elapsed:.2f}s status={pi_rc} ──")
    return totals


def network_metadata(line: str) -> dict | None:
    """Normalize one strace connect record without retaining payload or commands."""
    if "connect(" not in line:
        return None
    result = "pending"
    if " = " in line:
        result = line.rsplit(" = ", 1)[1].strip()[:80]
    inet = INET_RE.search(line)
    if inet:
        port = int(inet.group(1))
        address = inet.group(2)
        try:
            ip = ipaddress.ip_address(address)
            if ip.is_loopback:
                category = "loopback"
            elif ip.is_private:
                category = "private-network"
            else:
                category = "public-internet"
        except ValueError:
            category = "network-unknown"
        known = {
            ("127.0.0.1", 5098): "melious-control-relay",
            ("192.168.40.28", 9000): "local-qwen-provider",
            ("192.168.40.28", 8080): "searxng",
        }.get((address, port))
        return {"family": "ip", "destination": f"{address}:{port}",
                "category": known or category, "result": result}
    unix = UNIX_RE.search(line)
    if unix:
        path = unix.group(1)
        if "tuios" in path:
            category = "tuios-socket"
        elif "erpnext" in path:
            category = "erpnext-socket"
        elif "pi-task-control" in path:
            category = "pi-task-control-socket"
        elif "nix" in path:
            category = "nix-daemon-socket"
        else:
            category = "local-unix-socket"
        return {"family": "unix", "category": category, "result": result}
    return {"family": "unknown", "category": "unknown-connect", "result": result}


def audit_pi_event(event: dict, audit: AuditLogger) -> None:
    event_type = event.get("type")
    if event_type == "tool_execution_start":
        call_id = str(event.get("toolCallId") or "")[:128]
        audit.tool_started[call_id] = time.monotonic()
        audit.write("tool-start", tool=str(event.get("toolName") or "unknown")[:128],
                    call_id=call_id)
        print(f"── pi-task monitor: tool-start "
              f"{str(event.get('toolName') or 'unknown')[:128]} ──", file=sys.stderr)
    elif event_type == "tool_execution_end":
        failed = bool(event.get("isError"))
        call_id = str(event.get("toolCallId") or "")[:128]
        started = audit.tool_started.pop(call_id, None)
        audit.write("tool-end", tool=str(event.get("toolName") or "unknown")[:128],
                    call_id=call_id, result="error" if failed else "ok",
                    elapsed_ms=None if started is None else round(
                        (time.monotonic() - started) * 1000, 3))
        print(f"── pi-task monitor: tool-end "
              f"{str(event.get('toolName') or 'unknown')[:128]} "
              f"result={'error' if failed else 'ok'} ──", file=sys.stderr)
    elif event_type == "message_end":
        message = event.get("message") or {}
        if message.get("role") != "assistant":
            return
        usage = message.get("usage") or {}
        cost = usage.get("cost") or {}
        audit.write(
            "usage", model=str(message.get("model") or "")[:128],
            input=int(usage.get("input") or 0), output=int(usage.get("output") or 0),
            cache_read=int(usage.get("cacheRead") or 0),
            cost=float(cost.get("total") or 0) if isinstance(cost, dict) else 0,
        )


def monitored_run(command: list[str], events: Path, audit: AuditLogger) -> int:
    """Run the shared worker pipeline with live tool and connect telemetry."""
    read_fd, write_fd = os.pipe()
    traced = [
        "strace", "-f", "-qq", "-e", "trace=connect", "-s", "0",
        "-o", f"/proc/self/fd/{write_fd}", "--", *command,
    ]
    process = subprocess.Popen(traced, stdout=subprocess.PIPE, pass_fds=(write_fd,))
    os.close(write_fd)
    if process.stdout is None:
        os.close(read_fd)
        raise PolicyError("cannot monitor Pi event stream")
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "event")
    selector.register(read_fd, selectors.EVENT_READ, "network")
    buffers = {"event": b"", "network": b""}
    with events.open("wb") as event_file:
        while selector.get_map():
            for key, _ in selector.select():
                chunk = os.read(key.fd, 65536)
                stream = key.data
                if not chunk:
                    selector.unregister(key.fileobj)
                    tail = buffers[stream]
                    if tail:
                        lines = [tail]
                    else:
                        lines = []
                else:
                    data = buffers[stream] + chunk
                    parts = data.split(b"\n")
                    buffers[stream] = parts.pop()
                    lines = parts
                for raw in lines:
                    if stream == "event":
                        event_file.write(raw + b"\n")
                        event_file.flush()
                        try:
                            audit_pi_event(json.loads(raw), audit)
                        except (ValueError, TypeError):
                            continue
                    else:
                        metadata = network_metadata(raw.decode(errors="replace"))
                        if metadata:
                            audit.write("network-connect", **metadata)
                            print("── pi-task monitor: network "
                                  f"category={metadata['category']} "
                                  f"destination={metadata.get('destination', 'local')} "
                                  f"result={metadata['result']} ──", file=sys.stderr)
        event_file.flush()
    selector.close()
    process.stdout.close()
    os.close(read_fd)
    return process.wait()


def worker_rules(model: str, allowed: set[str], success: str, broad_scope: bool = False) -> str:
    allowed_text = "\n".join(f"- {name}" for name in sorted(allowed))
    scope = ("You may modify and create source files within this selected disposable app "
             "to complete the reviewed OpenSpec plan. All changes receive final diff review.\n"
             if broad_scope else
             "You may change exactly these project-relative files and no others:\n" + allowed_text)
    return (
        "You are the autonomous bounded implementation worker for exactly one "
        "self-contained OpenSpec work packet. Inspect the relevant project and Git "
        "context, implement the complete packet, run its acceptance command, diagnose "
        "failures, and iterate test/fix until it passes or you find a genuine blocker. "
        "Do not ask for step-by-step approval inside the authorized scope.\n"
        f"Your selected production model is {model}; never switch providers or models. "
        "Never commit, push, change Git refs, invoke OMP, delegate, or use another model. "
        "Make the smallest implementation needed.\n"
        "Melious is a trusted provider. You have the same allocated project, read-only "
        "Git history, shell tools, and MCP capabilities as the local worker.\n" +
        scope +
        "\nYour required acceptance command is:\n" + success +
        "\nRun that command yourself during implementation and repair failures within scope. "
        "The wrapper will independently run it again after you exit. End with a concise "
        "change/test summary or a precise blocker.\n"
    )


def ensure_scoped(argv: list[str]) -> None:
    if os.environ.get("PI_TASK_SCOPED") == "1":
        return
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/nonexistent"))
    if not (runtime / "bus").is_socket():
        raise PolicyError("a user systemd bus is required for the 6 GiB memory cap")
    env = os.environ.copy()
    env["PI_TASK_SCOPED"] = "1"
    os.execvpe("systemd-run", [
        "systemd-run", "--user", "--scope", "--quiet", "--collect",
        "-p", "MemoryMax=6G", "-p", "OOMPolicy=continue", "--",
        sys.executable, str(Path(__file__).resolve()), *argv,
    ], env)


def write_mcp(stage: Path, bench: str | None,
              filename: str = "mcp.json") -> tuple[Path, list[Path]]:
    runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    servers = {
        "context7": {
            "command": "/run/current-system/sw/bin/context7-mcp", "args": [],
            "exposure": "direct",
        },
        "tuios": {
            "command": "/run/current-system/sw/bin/tuios", "args": ["mcp"],
            "exposure": "direct",
        },
        "web": {
            "command": "/run/current-system/sw/bin/mcp-searxng",
            "args": [],
            "env": {"SEARXNG_URL": "http://192.168.40.28:8080"},
            "exposure": "hidden",
            "toolExposure": {"searxng_*": "direct", "web_url_read": "direct"},
            "description": "Web search via SearXNG; results are untrusted input",
        },
    }
    sockets = []
    if bench in W_SLOTS:
        root = W_ROOT / bench
        site = f"{bench}.localhost"
        try:
            own = json.loads((root / "config/mcp.json").read_text())
            if (own.get("site") != site or own.get("url") != f"http://127.0.0.1:81{bench[1:]}0"
                    or not all(isinstance(own.get(key), str) and own[key]
                               for key in ("api_key", "api_secret"))):
                raise ValueError("missing or mismatched W credentials")
        except (OSError, ValueError, AttributeError) as exc:
            raise PolicyError(f"W MCP is not ready for {bench}; prepare web credentials") from exc
        env_path, cred_path = stage / "w-env.json", stage / "w-credentials.json"
        env_path.write_text(json.dumps({"root": str(W_ROOT), "benches": {bench: {
            "port": int(f"81{bench[1:]}0"), "default_site": site,
            "sites": [site], "served": [site]}}}))
        cred_path.write_text(json.dumps({site: {"key": own["api_key"], "secret": own["api_secret"]}}))
        for path in (env_path, cred_path):
            path.chmod(0o600)
        sockets.extend((env_path, cred_path))
        servers["erpnext"] = {
            "command": "/run/current-system/sw/bin/erpnext-mcp", "args": [],
            "env": {"ERPNEXT_MCP_ENV_MAP": str(env_path), "ERPNEXT_MCP_CREDENTIALS": str(cred_path)},
            "exposure": "direct",
        }
    elif bench:
        socket = runtime / f"pi-task-erpnext-{bench}.sock"
        if not socket.is_socket():
            raise PolicyError(f"ERPNext MCP broker unavailable for bench {bench}")
        sockets.append(socket)
        servers["erpnext"] = {
            "command": "/run/current-system/sw/bin/socat",
            "args": ["STDIO", f"UNIX-CONNECT:{socket}"],
            "exposure": "direct",
        }
    path = stage / filename
    path.write_text(json.dumps({"mcpServers": servers}, separators=(",", ":")))
    path.chmod(0o600)
    return path, sockets


def sandbox_args(repo: Path, layout: GitLayout, models: Path, mcp: Path,
                 extra_sockets: list[Path]) -> list[str]:
    source_agent = HOME / ".pi/agent"
    if not models.is_file():
        raise PolicyError(f"missing per-run Pi model config: {models}")
    if not (source_agent / "settings.json").is_file():
        raise PolicyError(f"missing Pi config: {source_agent / 'settings.json'}")
    socket = Path(os.environ.get("TUIOS_SOCKET", ""))
    if not socket.is_socket() or not os.environ.get("TUIOS_PANE_ID"):
        raise PolicyError("pi-task must run inside a TUIOS pane for session-scoped MCP access")

    agent = HOME / ".pi/agent"
    args = [
        "bwrap", "--die-with-parent", "--unshare-pid", "--unshare-ipc",
        "--unshare-uts", "--new-session", "--clearenv",
        "--ro-bind", "/nix/store", "/nix/store",
        "--ro-bind", "/run/current-system", "/run/current-system",
        "--proc", "/proc", "--dev", "/dev",
        "--tmpfs", "/tmp", "--tmpfs", "/var", "--tmpfs", "/home",
        "--dir", str(HOME), "--dir", str(HOME / ".pi"), "--dir", str(agent),
        "--ro-bind", str(models), str(agent / "models.json"),
        "--ro-bind", str(source_agent / "settings.json"), str(agent / "settings.json"),
        "--ro-bind", str(mcp), str(agent / "mcp.json"),
    ]
    args += ["--ro-bind", "/etc", "/etc", "--ro-bind", "/bin", "/bin",
             "--ro-bind", "/usr", "/usr"]
    slot = w_slot(repo)
    if slot:
        args += ["--bind", str(slot), str(slot)]
        # Runtime state (DB sockets, logs, assets, virtualenvs) is writable. Git
        # metadata stays read-only for every installed app, not just this packet.
        for app in (slot / "apps").iterdir():
            target = app.resolve()
            if not target.is_relative_to(slot):
                raise PolicyError(f"W app escapes its environment: {app.name}")
            if (target / ".git").exists():
                args += git_mount_args(git_layout(target))
        args += git_mount_args(layout)
    else:
        args += project_mount_args(repo, layout)
    extension = source_agent / "extensions/tuios-agent-state.ts"
    if extension.is_file():
        args += ["--dir", str(agent / "extensions"), "--ro-bind", str(extension),
                 str(agent / "extensions/tuios-agent-state.ts")]
    daemon = Path("/nix/var/nix/daemon-socket")
    if (daemon / "socket").is_socket():
        args += ["--ro-bind", str(daemon), str(daemon), "--setenv", "NIX_REMOTE", "daemon"]
    args += ["--dir", "/run/user", "--dir", str(socket.parent.parent),
             "--dir", str(socket.parent), "--ro-bind", str(socket), str(socket)]
    for extra in extra_sockets:
        args += ["--ro-bind", str(extra), str(extra)]
    env = {
        "HOME": str(HOME), "USER": os.environ.get("USER", "cristian"),
        "PATH": "/run/current-system/sw/bin", "TERM": "dumb", "LANG": "C.UTF-8",
        "TMPDIR": "/tmp", "XDG_RUNTIME_DIR": str(socket.parent.parent),
        "GIT_CONFIG_NOSYSTEM": "1", "PI_CODING_AGENT_DIR": str(agent),
        "PI_OFFLINE": "1", "PI_TELEMETRY": "0", "PI_SKIP_VERSION_CHECK": "1",
        "DO_NOT_TRACK": "1", "OPENSPEC_TELEMETRY": "0",
    }
    env["SSL_CERT_FILE"] = "/etc/ssl/certs/ca-certificates.crt"
    env["NIX_SSL_CERT_FILE"] = "/etc/ssl/certs/ca-certificates.crt"
    if slot:
        env.update(E2E_BENCH=str(slot), E2E_SITE=f"{slot.name}.localhost",
                   E2E_BASE_URL=f"http://{slot.name}.localhost:81{slot.name[1:]}0",
                   PI_BASE_SHA=git(repo, "rev-parse", "HEAD", text=True).strip())
    for key in ("TUIOS_ENV", "TUIOS_HOST", "TUIOS_PANE_GRANTS", "TUIOS_PANE_ID",
                "TUIOS_PANE_TOKEN", "TUIOS_SESSION", "TUIOS_SOCKET", "TUIOS_WINDOW_ID"):
        if key in os.environ:
            env[key] = os.environ[key]
    for key, value in env.items():
        args += ["--setenv", key, value]
    return args + ["--chdir", str(repo)]


def worker_command(repo: Path, command: list[str]) -> list[str]:
    """Source the slot's development environment for Pi and every child tool."""
    slot = w_slot(repo)
    if not slot:
        return command
    return ["/run/current-system/sw/bin/bash", "-c",
            'source "$1/toolchain.sh"; '
            'if test -f /etc/pi-task-browser-env; then source /etc/pi-task-browser-env; fi; '
            'shift; exec "$@"', "pi-w-env", str(slot), *command]


def final_message(events: Path) -> str:
    answer = ""
    for line in events.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = event.get("message", {})
        if message.get("role") != "assistant":
            continue
        content = message.get("content", [])
        if isinstance(content, str):
            answer = content
        elif isinstance(content, list):
            answer = "".join(x.get("text", "") for x in content if x.get("type") == "text")
    return answer


def report(repo: Path, pi_rc: int, test_rc: int | None) -> None:
    print(f"── pi-task status: pi={pi_rc} success={'not-run' if test_rc is None else test_rc} ──")
    print("── git diff --stat ──")
    stat_out = git(repo, "diff", "--stat", "--no-ext-diff", text=True)
    print(stat_out.rstrip() or "(no tracked diff)")
    untracked = git(repo, "ls-files", "--others", "--exclude-standard", text=True).strip()
    if untracked:
        print("── untracked files ──")
        print(untracked)


def report_failure(attempt: int, classification: str, audit: AuditLogger) -> None:
    takeover = attempt >= 3
    audit.write("run-failure", attempt=attempt, classification=classification,
                takeover_required=takeover)
    print(f"── pi-task failure: attempt={attempt}/3 classification={classification} "
          f"takeover={'required' if takeover else 'not-yet'} ──", file=sys.stderr)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one bounded Pi implementation slice in the production worker pool")
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--model", choices=sorted(MODEL_SPECS), default="local-qwen",
                        help="allowlisted production model (default: local-qwen)")
    parser.add_argument("--worker-id", metavar="ID",
                        help="stable reviewer-assigned ID (required for melious-dsf)")
    parser.add_argument("--allow", action="append", default=[], metavar="FILE",
                        help="exact project-relative file Pi may change (repeatable)")
    parser.add_argument("--allow-app", action="store_true",
                        help="review all source changes in the selected disposable W app; permits new files")
    parser.add_argument("--erpnext-bench", choices=sorted(ALLOWED_BENCHES), metavar="BENCH",
                        help="exclusive test bench/database allocated to this worker")
    parser.add_argument("--attempt", type=int, choices=(1, 2, 3), default=1,
                        help="attempt number for the supervised three-failure takeover rule")
    parser.add_argument("--require-web", action="store_true",
                        help="fail admission when the optional SearXNG tool is unavailable")
    parser.add_argument("--success", required=True, metavar="COMMAND",
                        help="required success command, run in the same sandbox")
    parser.add_argument("brief", help="one bounded OpenSpec implementation slice")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    registered_worker = None
    final_outcome = "failed"
    audit = None
    attempt = 1
    pane_started = False
    try:
        args = parse_args(argv)
        attempt = args.attempt
        repo = validate_repo(args.cwd)
        layout = git_layout(repo)
        slot = validate_w_assignment(repo, layout, args.erpnext_bench)
        if args.allow_app and slot is None:
            raise PolicyError("--allow-app is only valid in a disposable W app checkout")
        allowed = validate_allowed(repo, args.allow) if not args.allow_app else set()
        spec = MODEL_SPECS[args.model]
        if args.model == "melious-dsf" and not args.worker_id:
            raise PolicyError("--worker-id is required for melious-dsf")
        if args.model != "melious-dsf" and args.worker_id:
            raise PolicyError("--worker-id is only valid for melious-dsf")
        if args.worker_id and not WORKER_RE.fullmatch(args.worker_id):
            raise PolicyError("--worker-id must contain only letters, digits, dot, underscore or dash")
        erp_root = HOME / "development/erpnext"
        is_erpnext = repo.is_relative_to(erp_root) or layout.common.is_relative_to(erp_root)
        if is_erpnext and slot is None:
            raise PolicyError("ERPNext workers require their own W1-W6 checkout and --erpnext-bench wN")
        if args.erpnext_bench and args.erpnext_bench not in W_SLOTS:
            raise PolicyError("ERPNext worker writes must use W1-W6, not shared test/UAT benches")
        ensure_scoped(argv)
        pane_started = True
        report_pane("working", "pi-task: admission and environment checks")
        runtime = Path(os.environ["XDG_RUNTIME_DIR"])
        worker_identity = args.worker_id or "local-qwen"
        run_id = f"{worker_identity}-a{args.attempt}-{int(time.time())}-{os.getpid()}"
        audit = AuditLogger(run_id, args.model, worker_identity)
        audit.write("run-start", model=spec["event_model"], attempt=args.attempt,
                    allowed_file_count=len(allowed), erpnext_bench=bool(args.erpnext_bench))
        web_ok, web_detail = web_admission(args.require_web)
        audit.write("capability", capability="searxng",
                    result="available" if web_ok else "degraded")
        if not web_ok:
            message = f"SearXNG MCP backend unavailable (degraded): {web_detail}"
            print(f"pi-task: WARNING: {message}; continuing because --require-web was not set",
                  file=sys.stderr)
        provider_lock = None
        if args.model == "melious-dsf":
            check_melious_health()
            registration = control_call(
                "_register", args.worker_id, "--reason",
                "start reviewer-approved bounded Pi slice")
            registered_worker = args.worker_id
            admission = registration["state"]
        else:
            provider_lock = acquire_exclusive_lock(runtime, "local-qwen")
            check_local_health()
            admission = "active"
        print(f"── pi-task admission: model={args.model} "
              f"worker={args.worker_id or 'local-qwen'} state={admission} cwd={repo} ──")

        lock_path = runtime / ("pi-task-" + hashlib.sha256(str(repo).encode()).hexdigest()[:16] + ".lock")
        lock = lock_path.open("w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PolicyError("another pi-task is already running for this project") from exc
        bench_lock = None
        if args.erpnext_bench:
            bench_lock = acquire_w_lease(slot)
            # Preparation may have completed between argument validation and the
            # lease. Re-resolve app linkage and Git metadata under the lease.
            repo = validate_repo(args.cwd)
            layout = git_layout(repo)
            slot = validate_w_assignment(repo, layout, args.erpnext_bench)
            for name in ("redis-cache.sock", "redis-queue.sock", "mariadb.sock"):
                if not (slot / "run" / name).is_socket():
                    raise PolicyError(f"prepare/start {slot.name} before launching: missing {name}")
            # Readiness is checked while holding the lifecycle lease; reset/stop
            # cannot race a running packet. Preparation never silently resets data.
            try:
                request = Request(f"http://127.0.0.1:81{slot.name[1:]}0/api/method/ping",
                                  headers={"Host": f"{slot.name}.localhost"})
                with urlopen(request, timeout=10) as response:
                    if response.status != 200:
                        raise ValueError("web service not ready")
            except (OSError, ValueError) as exc:
                raise PolicyError(f"prepare/start/web {slot.name} before launching: {exc}") from exc

        before = snapshot(repo)
        other_apps = w_app_snapshots(slot, repo) if slot else {}
        app_links = w_app_links(slot) if slot else {}
        with tempfile.TemporaryDirectory(prefix="pi-task.", dir=runtime) as tmp:
            stage = Path(tmp)
            inference_mcp, inference_sockets = write_mcp(stage, args.erpnext_bench)
            models = write_run_models(stage, HOME / ".pi/agent/models.json",
                                      args.model, args.worker_id)
            sb = sandbox_args(repo, layout, models, inference_mcp,
                              inference_sockets)
            rules = stage / "rules.md"
            task_rules = worker_rules(args.model, allowed, args.success, args.allow_app)
            rules.write_text(task_rules + (
                f"\nYour complete private development bench is {slot}; site {slot.name}.localhost. "
                "Its Python, Node, bench toolchain and linked app code are active in every tool. "
                "Run native tests, migrations, installs and asset builds here as needed; DB, logs, "
                "cache and runtime artifacts inside your W environment are writable. Source-file "
                "scope still applies to every app. Native suite example: cd \"$E2E_BENCH\" && "
                "env/bin/python config/pienv_run_tests.py test_annual_summary_native. "
                "Use bench --site \"$E2E_SITE\" migrate for schema changes, bench build for assets, "
                "pi-env-runtime reload after code/schema changes and pi-env-runtime worker for "
                "foreground background-job processing when needed. ERPNext MCP exposes only your "
                "own W site. Your app import must point to this packet checkout. "
                "Never target another environment or reset your slot.\n" if slot else ""))
            events = stage / "events.jsonl"
            prompt = f"Complete this OpenSpec work packet autonomously:\n\n{args.brief}"
            started = time.monotonic()
            model_args = ["-p", "--mode", "json", "--approve", "--no-session",
                          "--model", spec["pi_model"], "--append-system-prompt",
                          rules.read_text(), "--", prompt]
            report_pane("working", "pi-task: implementing and testing")
            pi_rc = monitored_run(sb + ["--", *worker_command(repo, ["pi", *model_args])], events, audit)
            elapsed = time.monotonic() - started
            try:
                accounting(events, args.model, elapsed, pi_rc)
            except PolicyError as exc:
                print(f"pi-task: REFUSED: {exc}", file=sys.stderr)
                report_failure(args.attempt, "model", audit)
                report(repo, pi_rc, None)
                return EXIT_POLICY
            answer = final_message(events)
            if answer:
                print(answer)
            after = snapshot(repo)
            effective_allowed = set(before["files"]) | set(after["files"]) if args.allow_app else allowed
            problems = policy_violations(before, after, effective_allowed) + w_scope_violations(other_apps)
            if slot and w_app_links(slot) != app_links:
                problems.append("installed W app links changed")
            if problems:
                for problem in problems:
                    print(f"pi-task: REFUSED: {problem}", file=sys.stderr)
                report_failure(args.attempt, "scope", audit)
                report(repo, pi_rc, None)
                return EXIT_POLICY
            test_rc: int | None = None
            if pi_rc == 0:
                report_pane("working", "pi-task: independent acceptance checks")
                # The success command uses the exact same sandbox capabilities and
                # 6 GiB outer scope as either trusted implementation provider.
                success_sb = sandbox_args(repo, layout, models, inference_mcp,
                                          inference_sockets)
                test_rc = run(success_sb + ["--", *worker_command(repo, [
                    "/run/current-system/sw/bin/bash", "-c", args.success])]).returncode
                after = snapshot(repo)
                effective_allowed = set(before["files"]) | set(after["files"]) if args.allow_app else allowed
                problems = policy_violations(before, after, effective_allowed) + w_scope_violations(other_apps)
                if slot and w_app_links(slot) != app_links:
                    problems.append("installed W app links changed")
                if problems:
                    for problem in problems:
                        print(f"pi-task: REFUSED after success command: {problem}", file=sys.stderr)
                    report_failure(args.attempt, "scope", audit)
                    report(repo, pi_rc, test_rc)
                    return EXIT_POLICY
            report(repo, pi_rc, test_rc)
            result_rc = pi_rc if pi_rc else (test_rc or 0)
            if result_rc == 0:
                final_outcome = "completed"
                audit.write("run-end", attempt=args.attempt, result="completed",
                            pi_status=pi_rc, success_status=test_rc,
                            elapsed_seconds=round(elapsed, 3))
            else:
                report_failure(args.attempt, "ambiguous" if pi_rc else "test", audit)
            return result_rc
    except PolicyError as exc:
        print(f"pi-task: {exc}", file=sys.stderr)
        if audit:
            report_failure(attempt, "wrapper-infrastructure", audit)
        return 2
    finally:
        if registered_worker:
            try:
                control_call("_finish", registered_worker, final_outcome, "--reason",
                             "pi-task wrapper exited")
            except PolicyError as exc:
                print(f"pi-task: failed to finalize worker {registered_worker}: {exc}",
                      file=sys.stderr)
        if pane_started:
            report_pane("done" if final_outcome == "completed" else "errored",
                        "pi-task: acceptance passed" if final_outcome == "completed"
                        else "pi-task: failed; inspect pane and audit log")


if __name__ == "__main__":
    raise SystemExit(main())
