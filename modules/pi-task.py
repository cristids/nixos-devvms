#!/usr/bin/env python3
"""Bounded production runner for Pi on devpro."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile


EXIT_POLICY = 6
HOME = Path("/home/cristian")
MODEL = "homelab/strata/qwen3.8-flash"


class PolicyError(RuntimeError):
    pass


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=False, **kwargs)


def git(repo: Path, *args: str, check: bool = True, text: bool = False):
    result = run(["git", "-C", str(repo), *args], stdout=subprocess.PIPE,
                 stderr=subprocess.PIPE, text=text)
    if check and result.returncode:
        err = result.stderr if text else result.stderr.decode(errors="replace")
        raise PolicyError(f"git {' '.join(args)} failed: {err.strip()}")
    return result.stdout


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
    dotgit = repo / ".git"
    if not dotgit.is_dir():
        raise PolicyError("pi-task requires a normal .git directory (not a linked worktree)")
    git(repo, "rev-parse", "--verify", "HEAD")
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
        # This is normally a submodule path from git ls-files.
        subhead = git(path, "rev-parse", "-q", "--verify", "HEAD", check=False,
                      text=True).strip()
        substatus = git(path, "status", "--porcelain=v1", "--untracked-files=all",
                        check=False)
        return "dir:" + subhead + ":" + hashlib.sha256(substatus).hexdigest()
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


def write_mcp(stage: Path, erpnext: bool) -> Path:
    servers = {
        "context7": {
            "command": "/run/current-system/sw/bin/context7-mcp", "args": [],
            "exposure": "direct",
        },
        "tuios": {
            "command": "/run/current-system/sw/bin/tuios", "args": ["mcp"],
            "exposure": "direct",
        },
    }
    if erpnext:
        creds_src = Path("/var/lib/erpnext-mcp/credentials.json")
        creds = stage / "erpnext-credentials.json"
        if creds_src.is_file():
            shutil.copyfile(creds_src, creds)
        else:
            creds.write_text("{}\n")
        creds.chmod(0o600)
        envmap = stage / "erpnext-env-map.json"
        result = run(["erpnext-mcp", "--dump-env-map"], stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE)
        envmap.write_bytes(result.stdout if result.returncode == 0 else b'{"benches":{}}\n')
        envmap.chmod(0o600)
        servers["erpnext"] = {
            "command": "/run/current-system/sw/bin/erpnext-mcp", "args": [],
            "exposure": "direct",
            "env": {
                "ERPNEXT_MCP_CREDENTIALS": str(HOME / ".pi/agent/erpnext-credentials.json"),
                "ERPNEXT_MCP_ENV_MAP": str(HOME / ".pi/agent/erpnext-env-map.json"),
            },
        }
    path = stage / "mcp.json"
    path.write_text(json.dumps({"mcpServers": servers}, separators=(",", ":")))
    path.chmod(0o600)
    return path


def sandbox_args(repo: Path, stage: Path, erpnext: bool) -> list[str]:
    source_agent = HOME / ".pi/agent"
    for name in ("models.json", "settings.json"):
        if not (source_agent / name).is_file():
            raise PolicyError(f"missing Pi config: {source_agent / name}")
    socket = Path(os.environ.get("TUIOS_SOCKET", ""))
    if not socket.is_socket() or not os.environ.get("TUIOS_PANE_ID"):
        raise PolicyError("pi-task must run inside a TUIOS pane for session-scoped MCP access")

    agent = HOME / ".pi/agent"
    args = [
        "bwrap", "--die-with-parent", "--unshare-pid", "--unshare-ipc",
        "--unshare-uts", "--new-session", "--clearenv",
        "--ro-bind", "/nix/store", "/nix/store",
        "--ro-bind", "/run/current-system", "/run/current-system",
        "--ro-bind", "/etc", "/etc", "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/usr", "/usr", "--proc", "/proc", "--dev", "/dev",
        "--tmpfs", "/tmp", "--tmpfs", "/var", "--tmpfs", "/home",
        "--dir", str(HOME), "--dir", str(HOME / ".pi"), "--dir", str(agent),
        "--ro-bind", str(source_agent / "models.json"), str(agent / "models.json"),
        "--ro-bind", str(source_agent / "settings.json"), str(agent / "settings.json"),
        "--ro-bind", str(stage / "mcp.json"), str(agent / "mcp.json"),
        "--bind", str(repo), str(repo), "--ro-bind", str(repo / ".git"), str(repo / ".git"),
    ]
    if erpnext:
        for name in ("erpnext-credentials.json", "erpnext-env-map.json"):
            args += ["--ro-bind", str(stage / name), str(agent / name)]
    extension = source_agent / "extensions/tuios-agent-state.ts"
    if extension.is_file():
        args += ["--dir", str(agent / "extensions"), "--ro-bind", str(extension),
                 str(agent / "extensions/tuios-agent-state.ts")]
    daemon = Path("/nix/var/nix/daemon-socket")
    if (daemon / "socket").is_socket():
        args += ["--ro-bind", str(daemon), str(daemon), "--setenv", "NIX_REMOTE", "daemon"]
    args += ["--dir", "/run/user", "--dir", str(socket.parent.parent),
             "--dir", str(socket.parent), "--ro-bind", str(socket), str(socket)]
    env = {
        "HOME": str(HOME), "USER": os.environ.get("USER", "cristian"),
        "PATH": "/run/current-system/sw/bin", "TERM": "dumb", "LANG": "C.UTF-8",
        "TMPDIR": "/tmp", "XDG_RUNTIME_DIR": str(socket.parent.parent),
        "SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt",
        "NIX_SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt",
        "GIT_CONFIG_NOSYSTEM": "1", "PI_CODING_AGENT_DIR": str(agent),
        "PI_OFFLINE": "1", "PI_TELEMETRY": "0", "PI_SKIP_VERSION_CHECK": "1",
        "DO_NOT_TRACK": "1", "OPENSPEC_TELEMETRY": "0",
    }
    for key in ("TUIOS_ENV", "TUIOS_HOST", "TUIOS_PANE_GRANTS", "TUIOS_PANE_ID",
                "TUIOS_PANE_TOKEN", "TUIOS_SESSION", "TUIOS_SOCKET", "TUIOS_WINDOW_ID"):
        if key in os.environ:
            env[key] = os.environ[key]
    for key, value in env.items():
        args += ["--setenv", key, value]
    return args + ["--chdir", str(repo)]


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
        description="Run one bounded Pi implementation slice using local Qwen")
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--allow", action="append", default=[], metavar="FILE",
                        help="exact project-relative file Pi may change (repeatable)")
    parser.add_argument("--success", required=True, metavar="COMMAND",
                        help="required success command, run in the same sandbox")
    parser.add_argument("brief", help="one bounded OpenSpec implementation slice")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parse_args(argv)
        repo = validate_repo(args.cwd)
        allowed = validate_allowed(repo, args.allow)
        ensure_scoped(argv)
        lock_path = Path(os.environ["XDG_RUNTIME_DIR"]) / ("pi-task-" + hashlib.sha256(str(repo).encode()).hexdigest()[:16] + ".lock")
        lock = lock_path.open("w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PolicyError("another pi-task is already running for this project") from exc

        before = snapshot(repo)
        with tempfile.TemporaryDirectory(prefix="pi-task.", dir=os.environ["XDG_RUNTIME_DIR"]) as tmp:
            stage = Path(tmp)
            erpnext = (HOME / "development/erpnext") == repo or (HOME / "development/erpnext") in repo.parents
            write_mcp(stage, erpnext)
            sb = sandbox_args(repo, stage, erpnext)
            allowed_text = "\n".join(f"- {name}" for name in sorted(allowed))
            rules = stage / "rules.md"
            rules.write_text(
                "You are the bounded implementation worker for exactly one OpenSpec slice.\n"
                "Use only the local homelab model already selected. Never commit, push, change Git refs, "
                "invoke OMP, delegate, or use cloud models. Make the smallest implementation needed.\n"
                "You may change exactly these project-relative files and no others:\n" + allowed_text +
                "\nThe wrapper, not you, runs the success command. End with a concise change summary.\n")
            events = stage / "events.jsonl"
            prompt = f"Implement this single bounded slice:\n\n{args.brief}"
            with events.open("wb") as output:
                pi_result = run(sb + ["--", "pi", "-p", "--mode", "json", "--approve",
                                      "--no-session", "--model", MODEL,
                                      "--append-system-prompt", rules.read_text(), "--", prompt],
                                stdout=output)
            pi_rc = pi_result.returncode
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
                test_rc = run(sb + ["--", "/run/current-system/sw/bin/bash", "-lc",
                                    args.success]).returncode
                problems = policy_violations(before, snapshot(repo), allowed)
                if problems:
                    for problem in problems:
                        print(f"pi-task: REFUSED after success command: {problem}", file=sys.stderr)
                    report(repo, pi_rc, test_rc)
                    return EXIT_POLICY
            report(repo, pi_rc, test_rc)
            return pi_rc if pi_rc else (test_rc or 0)
    except PolicyError as exc:
        print(f"pi-task: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
