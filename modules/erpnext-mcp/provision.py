"""erpnext-mcp-provision: create/refresh the MCP API user on every served dev site.

For each bench/site the MCP discovers (same rules as the server), runs
provision_site.py with that bench's python under the workspace toolchain
(`direnv exec ~/development/erpnext <bench>/env/bin/python …`, cwd <bench>/sites;
the convention of benches/mailpit_flush.py) and collects the keys into
$ERPNEXT_MCP_CREDENTIALS (default /var/lib/erpnext-mcp/credentials.json, 0600).

Roles: System Manager everywhere, except read-only benches
($ERPNEXT_MCP_READONLY_BENCHES, default uat) which get read-oriented "* User" roles.
Idempotent; re-run after a new bench/site or a `testenv.sh refresh`. Prints a
per-site summary, never a key or secret. Exit 1 if any site failed (its previous
entry is kept).
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # run with -I
import erpnext_mcp as m  # noqa: E402

WORKSPACE = os.environ.get("ERPNEXT_WORKSPACE", "/home/cristian/development/erpnext")
HELPER = str(Path(__file__).with_name("provision_site.py"))
DIRENV = os.environ.get("DIRENV", "direnv")


def main() -> int:
    env = m.discover()
    root = Path(env["root"])
    readonly = m._readonly_benches()
    cred_path = Path(m._credentials_path())
    old = m._load_credentials()
    new: dict = {}
    failed = []
    with tempfile.TemporaryDirectory(prefix="erpnext-mcp-prov.") as tmp:
        os.chmod(tmp, 0o700)
        for bench, b in sorted(env["benches"].items()):
            mode = "read" if bench in readonly else "full"
            for site in b["sites"]:
                out = Path(tmp) / f"{site}.json"
                cmd = [DIRENV, "exec", WORKSPACE, str(root / bench / "env" / "bin" / "python"),
                       HELPER, site, mode, str(out)]
                try:
                    proc = subprocess.run(cmd, cwd=root / bench / "sites", capture_output=True,
                                          text=True, timeout=300)
                    ok = proc.returncode == 0 and out.is_file()
                except subprocess.TimeoutExpired:
                    proc, ok = None, False
                if not ok:
                    tail = (proc.stderr.strip().splitlines()[-1:] if proc else ["timeout"])
                    print(f"FAIL {site} (bench {bench}): {' '.join(tail)[:300]}", file=sys.stderr)
                    failed.append(site)
                    if site in old:
                        new[site] = old[site]
                    continue
                res = json.loads(out.read_text())
                new[site] = {"bench": bench, "port": b["port"], "user": res["user"],
                             "key": res["key"], "secret": res["secret"], "roles": res["roles"]}
                print(f"ok   {site} (bench {bench}, :{b['port']}, {mode}): "
                      f"{'created user, ' if res['created'] else ''}"
                      f"{'new key' if res['new_key'] else 'key reused'}; roles {', '.join(res['roles'])}")
    cred_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = cred_path.with_name(cred_path.name + ".tmp")
    fd = os.open(tmp_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(new, fh, indent=1, sort_keys=True)
    os.replace(tmp_file, cred_path)
    print(f"wrote {cred_path}: {len(new)} site(s){', FAILED: ' + ', '.join(failed) if failed else ''}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
