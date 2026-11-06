#!/usr/bin/env python3
"""Bounded production runner for Pi on devpro."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen


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
SEARXNG_HEALTH = "http://192.168.40.28:8080/healthz"


class PolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitLayout:
    dotgit: Path
    gitdir: Path
    common: Path


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
    return any(path.is_relative_to(root) for root in roots)


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
    if not any(repo.is_relative_to(root) and repo != root for root in roots):
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


def check_web_mcp_health() -> None:
    try:
        with urlopen(SEARXNG_HEALTH, timeout=5) as response:
            if response.status != 200:
                raise ValueError(f"HTTP {response.status}")
    except (OSError, URLError, ValueError, AttributeError) as exc:
        raise PolicyError(f"SearXNG MCP backend unavailable: {exc}") from exc


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
    result = run(["pi-task-control", *args], stdout=subprocess.PIPE,
                 stderr=subprocess.PIPE, text=True)
    if result.returncode:
        raise PolicyError(
            f"DSF control request failed: {(result.stderr or result.stdout).strip()}")
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise PolicyError("DSF control returned invalid status") from exc


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
    if bench:
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
    for key in ("TUIOS_ENV", "TUIOS_HOST", "TUIOS_PANE_GRANTS", "TUIOS_PANE_ID",
                "TUIOS_PANE_TOKEN", "TUIOS_SESSION", "TUIOS_SOCKET", "TUIOS_WINDOW_ID"):
        if key in os.environ:
            env[key] = os.environ[key]
    for key, value in env.items():
        args += ["--setenv", key, value]
    return args + ["--chdir", str(repo)]


def pi_command(spec: dict, model_args: list[str]) -> list[str]:
    return ["pi", *model_args]


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
    parser.add_argument("--erpnext-bench", choices=sorted(ALLOWED_BENCHES), metavar="BENCH",
                        help="exclusive test bench/database allocated to this worker")
    parser.add_argument("--success", required=True, metavar="COMMAND",
                        help="required success command, run in the same sandbox")
    parser.add_argument("brief", help="one bounded OpenSpec implementation slice")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    registered_worker = None
    final_outcome = "failed"
    try:
        args = parse_args(argv)
        repo = validate_repo(args.cwd)
        layout = git_layout(repo)
        allowed = validate_allowed(repo, args.allow)
        spec = MODEL_SPECS[args.model]
        if args.model == "melious-dsf" and not args.worker_id:
            raise PolicyError("--worker-id is required for melious-dsf")
        if args.model != "melious-dsf" and args.worker_id:
            raise PolicyError("--worker-id is only valid for melious-dsf")
        erp_root = HOME / "development/erpnext"
        is_erpnext = repo.is_relative_to(erp_root) or layout.common.is_relative_to(erp_root)
        if is_erpnext and not args.erpnext_bench:
            raise PolicyError("ERPNext work requires an exclusive --erpnext-bench test/test1..test7")
        ensure_scoped(argv)
        runtime = Path(os.environ["XDG_RUNTIME_DIR"])
        check_web_mcp_health()
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
            bench_lock = acquire_exclusive_lock(runtime, f"erpnext-{args.erpnext_bench}")

        before = snapshot(repo)
        with tempfile.TemporaryDirectory(prefix="pi-task.", dir=runtime) as tmp:
            stage = Path(tmp)
            inference_mcp, inference_sockets = write_mcp(stage, args.erpnext_bench)
            models = write_run_models(stage, HOME / ".pi/agent/models.json",
                                      args.model, args.worker_id)
            sb = sandbox_args(repo, layout, models, inference_mcp,
                              inference_sockets)
            allowed_text = "\n".join(f"- {name}" for name in sorted(allowed))
            rules = stage / "rules.md"
            rules.write_text(
                "You are the bounded implementation worker for exactly one OpenSpec slice.\n"
                f"Your selected production model is {args.model}; never switch providers or models. "
                "Never commit, push, change Git refs, invoke OMP, delegate, or use another model. "
                "Make the smallest implementation needed.\n"
                "Melious is a trusted provider. You have the same allocated project, read-only "
                "Git history, shell tools, and MCP capabilities as the local worker.\n"
                "You may change exactly these project-relative files and no others:\n" + allowed_text +
                "\nThe wrapper, not you, runs the success command. End with a concise change summary.\n")
            events = stage / "events.jsonl"
            prompt = f"Implement this single bounded slice:\n\n{args.brief}"
            started = time.monotonic()
            model_args = ["-p", "--mode", "json", "--approve", "--no-session",
                          "--model", spec["pi_model"], "--append-system-prompt",
                          rules.read_text(), "--", prompt]
            with events.open("wb") as output:
                pi_result = run(sb + ["--", *pi_command(spec, model_args)],
                                stdout=output)
            pi_rc = pi_result.returncode
            elapsed = time.monotonic() - started
            try:
                accounting(events, args.model, elapsed, pi_rc)
            except PolicyError as exc:
                print(f"pi-task: REFUSED: {exc}", file=sys.stderr)
                report(repo, pi_rc, None)
                return EXIT_POLICY
            answer = final_message(events)
            if answer:
                print(answer)
            problems = policy_violations(before, snapshot(repo), allowed)
            if problems:
                for problem in problems:
                    print(f"pi-task: REFUSED: {problem}", file=sys.stderr)
                report(repo, pi_rc, None)
                return EXIT_POLICY
            test_rc: int | None = None
            if pi_rc == 0:
                # The success command uses the exact same sandbox capabilities and
                # 6 GiB outer scope as either trusted implementation provider.
                success_sb = sandbox_args(repo, layout, models, inference_mcp,
                                          inference_sockets)
                test_rc = run(success_sb + ["--", "/run/current-system/sw/bin/bash", "-lc",
                                            args.success]).returncode
                problems = policy_violations(before, snapshot(repo), allowed)
                if problems:
                    for problem in problems:
                        print(f"pi-task: REFUSED after success command: {problem}", file=sys.stderr)
                    report(repo, pi_rc, test_rc)
                    return EXIT_POLICY
            report(repo, pi_rc, test_rc)
            result_rc = pi_rc if pi_rc else (test_rc or 0)
            if result_rc == 0:
                final_outcome = "completed"
            return result_rc
    except PolicyError as exc:
        print(f"pi-task: {exc}", file=sys.stderr)
        return 2
    finally:
        if registered_worker:
            try:
                control_call("_finish", registered_worker, final_outcome, "--reason",
                             "pi-task wrapper exited")
            except PolicyError as exc:
                print(f"pi-task: failed to finalize worker {registered_worker}: {exc}",
                      file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
