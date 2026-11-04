"""ERPNext MCP server (stdio) for the devpro dev benches only.

Environments are DISCOVERED, not hard-coded: every bench under $ERPNEXT_MCP_BENCHES
(default ~cristian/development/erpnext/benches) whose sites/common_site_config.json
has a webserver_port is one environment; its sites are the sites/<name>/ dirs that
hold a site_config.json. Benches without a webserver_port are not served and are
skipped. `bench serve` without --site is pinned to the bench's default_site (frappe
bench_helper falls back to it), so when default_site is set only that site answers
over HTTP; the bench's other sites are listed with served=false and refused. Inside the omp-task sandbox (no site_config.json there) the wrapper passes
the same map pre-built as JSON in $ERPNEXT_MCP_ENV_MAP instead.

Allowlist: a request only ever goes to http://127.0.0.1:<port of a discovered
bench> with a Host header that is one of THAT bench's sites. There is no way to pass
a URL, so it can never reach prod or any other host.

Site selection: every tool takes `site` — a site name ("test3.localhost") or a short
alias ("test3", "leave-rename-6eb9b147e2e4"). Without it the default is the bench
(or site dir) containing the server's cwd, when that names exactly one site;
otherwise the call fails and asks for `site`. Never a silent fallback.

Auth: Frappe token auth for the `mcp-dev@mcp.localhost` API user, per site, from
$ERPNEXT_MCP_CREDENTIALS (default /var/lib/erpnext-mcp/credentials.json, mode 0600,
{"<site>": {"key": …, "secret": …}}), written by `erpnext-mcp-provision`. Missing
credentials do not stop the server: that site's calls return a clear error.

Read-only benches ($ERPNEXT_MCP_READONLY_BENCHES, default "uat": UAT holds a restore
of production data and is the user's acceptance environment): create_doc/update_doc
are refused there and call_method is GET-only.

Tools: list_environments, get_doctype_meta, list_docs, get_doc, run_report,
call_method, create_doc, update_doc. Deliberately NO delete: no tool issues HTTP
DELETE and call_method refuses delete/cancel/rename-style methods.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

DEFAULT_BENCHES = "/home/cristian/development/erpnext/benches"
DEFAULT_CREDENTIALS = "/var/lib/erpnext-mcp/credentials.json"
DEFAULT_READONLY_BENCHES = "uat"
MAX_LIMIT = 500
TIMEOUT = 60.0

_HOSTNAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)+$")
_BENCH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

# Methods that destroy or irreversibly change data. call_method refuses these
# (create/update are allowed; the server exposes no delete at all).
_DENY_METHOD = re.compile(
    r"(^|[._])(delete|bulk_delete|delete_items|remove|cancel|bulk_cancel|"
    r"rename_doc|rename|drop|truncate|purge|amend)($|[._])",
    re.IGNORECASE,
)
_METHOD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")

# Test hook: tests swap in an httpx.MockTransport.
TRANSPORT: httpx.BaseTransport | None = None

mcp = FastMCP(
    "erpnext",
    instructions=(
        "ERPNext dev benches on devpro (test, test1..test6, uat, ...). Call "
        "list_environments first. Pass `site` (e.g. 'test3' or 'test3.localhost') "
        "on every call unless you work inside that bench's directory. Read with "
        "get_doctype_meta/list_docs/get_doc/run_report, write with create_doc/"
        "update_doc (not on read-only benches such as uat). Deleting is not possible."
    ),
)


# ── discovery ────────────────────────────────────────────────────────────────


def _valid_port(value: Any) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 1024 <= port <= 65535 else None


def discover(root: str | None = None) -> dict:
    """Scan the benches dir: {"root": path, "benches": {name: {port, default_site, sites}}}."""
    root_path = Path(root or os.environ.get("ERPNEXT_MCP_BENCHES", DEFAULT_BENCHES))
    benches: dict[str, dict] = {}
    try:
        entries = sorted(p for p in root_path.iterdir() if p.is_dir())
    except OSError:
        entries = []
    for bench in entries:
        if not _BENCH_NAME.match(bench.name):
            continue
        try:
            common = json.loads((bench / "sites" / "common_site_config.json").read_text())
        except (OSError, ValueError):
            continue
        port = _valid_port(common.get("webserver_port")) if isinstance(common, dict) else None
        if port is None:
            continue
        sites = sorted(
            s.name
            for s in (bench / "sites").iterdir()
            if s.is_dir() and _HOSTNAME.match(s.name) and (s / "site_config.json").is_file()
        )
        if sites:
            default_site = common.get("default_site")
            benches[bench.name] = {
                "port": port,
                "default_site": default_site,
                "sites": sites,
                "served": [default_site] if default_site in sites else sites,
            }
    return {"root": str(root_path), "benches": benches}


def _env_map() -> dict:
    """The environment map: pre-built JSON from $ERPNEXT_MCP_ENV_MAP, else discovered."""
    path = os.environ.get("ERPNEXT_MCP_ENV_MAP")
    if not path:
        return discover()
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise ToolError(f"cannot read environment map {path}: {exc}") from exc
    # Re-validate: the map is input, the allowlist rules still apply.
    benches = {}
    for name, b in (raw.get("benches") or {}).items():
        port = _valid_port(b.get("port"))
        sites = sorted(s for s in b.get("sites", []) if isinstance(s, str) and _HOSTNAME.match(s))
        served = [s for s in b.get("served", sites) if s in sites]
        if _BENCH_NAME.match(name) and port and sites:
            benches[name] = {"port": port, "default_site": b.get("default_site"),
                             "sites": sites, "served": served}
    return {"root": str(raw.get("root") or DEFAULT_BENCHES), "benches": benches}


def _site_index(env: dict, served_only: bool = True) -> dict[str, tuple[str, int]]:
    """site -> (bench, port). A site name present in two benches is dropped (ambiguous)."""
    index: dict[str, tuple[str, int]] = {}
    dup: set[str] = set()
    for bench, b in env["benches"].items():
        for site in b["served"] if served_only else b["sites"]:
            if site in index:
                dup.add(site)
            index[site] = (bench, b["port"])
    for site in dup:
        index.pop(site)
    return index


def _readonly_benches() -> set[str]:
    raw = os.environ.get("ERPNEXT_MCP_READONLY_BENCHES", DEFAULT_READONLY_BENCHES)
    return {b.strip() for b in raw.split(",") if b.strip()}


def _cwd_default(env: dict) -> tuple[str | None, str]:
    """(site, reason) for the bench containing the cwd; site None when ambiguous."""
    try:
        cwd = Path(os.getcwd()).resolve()
    except OSError:
        return None, "cwd unavailable"
    root = Path(env["root"])
    try:
        root = root.resolve()
    except OSError:
        pass
    try:
        rel = cwd.relative_to(root).parts
    except ValueError:
        return None, f"cwd {cwd} is not inside a bench under {root}"
    if not rel or rel[0] not in env["benches"]:
        return None, f"cwd {cwd} is not inside a served bench"
    bench = env["benches"][rel[0]]
    if len(rel) >= 3 and rel[1] == "sites" and rel[2] in bench["sites"]:
        return rel[2], f"cwd is in site dir {rel[2]}"
    if len(bench["served"]) == 1:
        return bench["served"][0], f"cwd is in bench {rel[0]}"
    return None, f"bench {rel[0]} serves several sites ({', '.join(bench['served'])})"


def resolve_site(site: str | None) -> tuple[str, str, int]:
    """(site, bench, port) for a site name/alias, or the cwd default when site is empty."""
    env = _env_map()
    index = _site_index(env)
    unserved = _site_index(env, served_only=False)

    def found(name: str) -> tuple[str, str, int]:
        if name in index:
            return name, *index[name]
        bench, port = unserved[name]
        raise ToolError(
            f"site '{name}' exists in bench {bench} but is not served over HTTP: that "
            f"bench's web server (:{port}) is pinned to its default_site "
            f"{env['benches'][bench]['default_site']}. Use the bench CLI for it."
        )

    if not site:
        default, reason = _cwd_default(env)
        if default is None or default not in unserved:
            raise ToolError(
                f"no `site` given and no unambiguous default ({reason}). Pass site= one of: "
                f"{', '.join(sorted(index)) or '(none discovered)'}"
            )
        return found(default)
    s = site.strip().lower()
    for name in (s, s + ".localhost"):
        if name in unserved:
            return found(name)
    if s in env["benches"] and len(env["benches"][s]["served"]) == 1:
        only = env["benches"][s]["served"][0]
        if only in index:
            return only, *index[only]
    raise ToolError(
        f"unknown site '{site}': allowed sites are {', '.join(sorted(index)) or '(none discovered)'}"
    )


# ── credentials ──────────────────────────────────────────────────────────────


def _credentials_path() -> str:
    return os.environ.get("ERPNEXT_MCP_CREDENTIALS", DEFAULT_CREDENTIALS)


def _load_credentials() -> dict:
    try:
        data = json.loads(Path(_credentials_path()).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _has_credentials(entry: Any) -> bool:
    return isinstance(entry, dict) and bool(entry.get("key")) and bool(entry.get("secret"))


def _credentials(site: str) -> tuple[str, str]:
    entry = _load_credentials().get(site)
    if not _has_credentials(entry):
        raise ToolError(
            f"no credentials for site '{site}' in {_credentials_path()}: run "
            f"`systemctl --user start erpnext-mcp-provision` (or `erpnext-mcp-provision`) on devpro"
        )
    return entry["key"], entry["secret"]


# ── HTTP ─────────────────────────────────────────────────────────────────────


def _error_text(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:500]
    if not isinstance(body, dict):
        return resp.text[:500]
    parts = []
    for k in ("exc_type", "exception", "message"):
        if body.get(k):
            parts.append(str(body[k])[:500])
    if body.get("_server_messages"):
        try:
            msgs = json.loads(body["_server_messages"])
            for m in msgs:
                try:
                    parts.append(str(json.loads(m).get("message", m))[:500])
                except (ValueError, AttributeError):
                    parts.append(str(m)[:500])
        except ValueError:
            pass
    return " | ".join(parts) or resp.text[:500]


def _request(
    site: str | None,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
) -> Any:
    method = method.upper()
    if method not in ("GET", "POST", "PUT"):
        raise ToolError(f"HTTP {method} is not allowed by this server")
    if not path.startswith("/api/"):
        raise ToolError("only Frappe /api/ paths are allowed")
    host, bench, port = resolve_site(site)
    key, secret = _credentials(host)
    headers = {
        "Authorization": f"token {key}:{secret}",
        "Host": host,
        "Accept": "application/json",
    }
    with httpx.Client(
        base_url=f"http://127.0.0.1:{port}",
        headers=headers,
        timeout=TIMEOUT,
        follow_redirects=False,
        transport=TRANSPORT,
    ) as client:
        try:
            resp = client.request(method, path, params=params, json=json_body)
        except httpx.HTTPError as exc:
            raise ToolError(f"{host}: request failed: {exc}") from exc
    if resp.status_code >= 300:
        hint = ""
        if resp.status_code in (401, 403):
            hint = " (stale keys after a refresh? re-run erpnext-mcp-provision)"
        raise ToolError(f"{host}: HTTP {resp.status_code}: {_error_text(resp)}{hint}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise ToolError(f"{host}: non-JSON response") from exc
    if isinstance(body, dict):
        if "data" in body:
            return body["data"]
        if "message" in body:
            return body["message"]
    return body


def _writable(site: str | None) -> None:
    host, bench, _ = resolve_site(site)
    if bench in _readonly_benches():
        raise ToolError(f"{host} (bench {bench}) is read-only through this server")


def _q(segment: str) -> str:
    if not segment or not segment.strip():
        raise ToolError("doctype/name must not be empty")
    return quote(segment, safe="")


# ── tools ────────────────────────────────────────────────────────────────────


def list_environments() -> list[dict]:
    """All dev bench sites: bench, site, port, served over HTTP, credentials present, read-only, cwd default."""
    env = _env_map()
    index = _site_index(env, served_only=False)
    creds = _load_credentials()
    readonly = _readonly_benches()
    default, _ = _cwd_default(env)
    return [
        {
            "bench": bench,
            "site": site,
            "port": b["port"],
            "served": site in b["served"],
            "credentials": _has_credentials(creds.get(site)),
            "read_only": bench in readonly,
            "default": site == default,
        }
        for bench, b in sorted(env["benches"].items())
        for site in b["sites"]
        if site in index
    ]


def get_doctype_meta(doctype: str, site: str | None = None) -> dict:
    """Fields of a DocType (including custom fields): fieldname, type, label, options, reqd."""
    res = _request(
        site,
        "GET",
        "/api/method/frappe.desk.form.load.getdoctype",
        params={"doctype": doctype},
    )
    # getdoctype puts its docs on the top-level response, not under "message".
    docs = res.get("docs", []) if isinstance(res, dict) else []
    meta = next((d for d in docs if d.get("name") == doctype), docs[0] if docs else {})
    fields = [
        {
            k: f.get(k)
            for k in ("fieldname", "fieldtype", "label", "options", "reqd")
            if f.get(k) not in (None, "", 0)
        }
        for f in meta.get("fields", [])
        if f.get("fieldtype") not in ("Section Break", "Column Break", "Tab Break")
    ]
    return {
        "name": meta.get("name", doctype),
        "module": meta.get("module"),
        "is_submittable": meta.get("is_submittable"),
        "istable": meta.get("istable"),
        "fields": fields,
    }


def list_docs(
    doctype: str,
    filters: list | dict | None = None,
    fields: list[str] | None = None,
    limit: int = 20,
    order_by: str | None = None,
    site: str | None = None,
) -> list:
    """List documents. filters: Frappe filters ([[field, op, value], ...] or {field: value})."""
    params: dict[str, Any] = {
        "fields": json.dumps(fields or ["name"]),
        "limit_page_length": max(1, min(int(limit), MAX_LIMIT)),
    }
    if filters:
        params["filters"] = json.dumps(filters)
    if order_by:
        params["order_by"] = order_by
    return _request(site, "GET", f"/api/resource/{_q(doctype)}", params=params)


def get_doc(doctype: str, name: str, site: str | None = None) -> dict:
    """Fetch one document with its child tables."""
    return _request(site, "GET", f"/api/resource/{_q(doctype)}/{_q(name)}")


def run_report(report_name: str, filters: dict | None = None, site: str | None = None) -> dict:
    """Run a query/script report; returns columns and result rows."""
    return _request(
        site,
        "POST",
        "/api/method/frappe.desk.query_report.run",
        json_body={"report_name": report_name, "filters": filters or {}},
    )


def call_method(
    method: str, args: dict | None = None, http_method: str = "POST", site: str | None = None
) -> Any:
    """Call a whitelisted server method (dotted path). Delete/cancel/rename-type methods are refused; GET only on read-only benches."""
    if not _METHOD_NAME.match(method):
        raise ToolError(f"invalid method name '{method}'")
    if _DENY_METHOD.search(method):
        raise ToolError(f"method '{method}' is refused: destructive methods are not allowed")
    http_method = http_method.upper()
    if http_method not in ("GET", "POST"):
        raise ToolError("http_method must be GET or POST")
    if http_method == "POST":
        _writable(site)
    path = f"/api/method/{method}"
    if http_method == "GET":
        return _request(site, "GET", path, params=args or {})
    return _request(site, "POST", path, json_body=args or {})


def create_doc(doctype: str, data: dict, site: str | None = None) -> dict:
    """Create a document (not submitted). data: field values, child tables as lists."""
    _writable(site)
    return _request(site, "POST", f"/api/resource/{_q(doctype)}", json_body=data)


def update_doc(doctype: str, name: str, data: dict, site: str | None = None) -> dict:
    """Update fields of an existing document."""
    _writable(site)
    return _request(site, "PUT", f"/api/resource/{_q(doctype)}/{_q(name)}", json_body=data)


for _fn in (
    list_environments,
    get_doctype_meta,
    list_docs,
    get_doc,
    run_report,
    call_method,
    create_doc,
    update_doc,
):
    mcp.tool(_fn)


def main() -> None:
    # `erpnext-mcp --dump-env-map`: print the discovered map (no secrets) for the
    # omp-task wrapper, which passes it into the sandbox as $ERPNEXT_MCP_ENV_MAP.
    if sys.argv[1:] == ["--dump-env-map"]:
        print(json.dumps(discover(), indent=1))
        return
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
