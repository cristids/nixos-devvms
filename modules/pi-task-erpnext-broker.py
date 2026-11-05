#!/usr/bin/env python3
"""Launch one bench-filtered ERPNext MCP over a systemd-accepted stdio socket."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ALLOWED = {"test", *{f"test{i}" for i in range(1, 8)}}
CREDS = Path("/var/lib/erpnext-mcp/credentials.json")


def filtered(bench: str, env_payload: dict, credentials: dict) -> tuple[dict, dict]:
    if bench not in ALLOWED:
        raise RuntimeError(f"bench refused: {bench}")
    data = env_payload.get("benches", {}).get(bench)
    if not data:
        raise RuntimeError(f"bench unavailable: {bench}")
    sites = set(data.get("served", []))
    selected = {site: value for site, value in credentials.items() if site in sites}
    if sites - selected.keys():
        raise RuntimeError(f"credentials unavailable for bench: {bench}")
    return ({"root": env_payload.get("root"), "benches": {bench: data}}, selected)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: pi-task-erpnext-broker BENCH", file=sys.stderr)
        return 2
    bench = sys.argv[1]
    result = subprocess.run(["/run/current-system/sw/bin/erpnext-mcp", "--dump-env-map"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        print("ERPNext environment discovery failed", file=sys.stderr)
        return 2
    try:
        envmap, creds = filtered(bench, json.loads(result.stdout), json.loads(CREDS.read_text()))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERPNext MCP broker: {exc}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="pi-task-erpnext.") as tmp:
        root = Path(tmp)
        env_path = root / "env.json"
        cred_path = root / "credentials.json"
        env_path.write_text(json.dumps(envmap, separators=(",", ":")))
        cred_path.write_text(json.dumps(creds, separators=(",", ":")))
        env_path.chmod(0o600)
        cred_path.chmod(0o600)
        env = os.environ.copy()
        env["ERPNEXT_MCP_ENV_MAP"] = str(env_path)
        env["ERPNEXT_MCP_CREDENTIALS"] = str(cred_path)
        os.execve("/run/current-system/sw/bin/erpnext-mcp", ["erpnext-mcp"], env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
