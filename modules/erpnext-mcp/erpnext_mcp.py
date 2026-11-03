"""ERPNext MCP server (stdio) for the devpro dev benches only.

Targets exactly two Frappe benches on devpro, hard-coded below:
  test -> http://127.0.0.1:8000 (site test.localhost)
  uat  -> http://127.0.0.1:8001 (site uat.localhost)
There is no way to point it at any other URL, so it can never reach prod.

Auth: Frappe token auth for the `mcp-dev` API user, one key/secret pair per site,
read from the environment (ERPNEXT_TEST_KEY / ERPNEXT_TEST_SECRET,
ERPNEXT_UAT_KEY / ERPNEXT_UAT_SECRET) or, if unset, from the env file
$ERPNEXT_MCP_ENV_FILE (default /var/lib/erpnext-mcp.env, KEY=VALUE lines).
Missing credentials do not stop the server: each tool call returns a clear
"no credentials" error instead.

Tools: get_doctype_meta, list_docs, get_doc, run_report, call_method,
create_doc, update_doc. Deliberately NO delete: no tool issues HTTP DELETE and
call_method refuses delete/cancel/rename-style methods.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import quote

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

SITES: dict[str, tuple[str, str]] = {
    "test": ("http://127.0.0.1:8000", "test.localhost"),
    "uat": ("http://127.0.0.1:8001", "uat.localhost"),
}
DEFAULT_ENV_FILE = "/var/lib/erpnext-mcp.env"
MAX_LIMIT = 500
TIMEOUT = 60.0

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
        "ERPNext dev benches on devpro: site 'test' (default) and 'uat'. "
        "Read with get_doctype_meta/list_docs/get_doc/run_report, write with "
        "create_doc/update_doc. Deleting is not possible through this server."
    ),
)


def _read_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip().removeprefix("export ").strip()
                values[k] = v.strip().strip("'\"")
    except OSError:
        pass
    return values


def _credentials(site: str) -> tuple[str, str]:
    prefix = f"ERPNEXT_{site.upper()}_"
    key = os.environ.get(prefix + "KEY")
    secret = os.environ.get(prefix + "SECRET")
    env_file = os.environ.get("ERPNEXT_MCP_ENV_FILE", DEFAULT_ENV_FILE)
    if not (key and secret):
        file_vals = _read_env_file(env_file)
        key = key or file_vals.get(prefix + "KEY")
        secret = secret or file_vals.get(prefix + "SECRET")
    if not (key and secret):
        raise ToolError(
            f"no credentials for site '{site}': set {prefix}KEY and {prefix}SECRET "
            f"(API key/secret of the mcp-dev user on {SITES[site][1]}) in {env_file}"
        )
    return key, secret


def _site(site: str) -> tuple[str, str]:
    if site not in SITES:
        raise ToolError(f"unknown site '{site}': allowed sites are {sorted(SITES)}")
    return SITES[site]


def _error_text(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
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
    site: str,
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
    base_url, host = _site(site)
    key, secret = _credentials(site)
    headers = {
        "Authorization": f"token {key}:{secret}",
        "Host": host,
        "Accept": "application/json",
    }
    with httpx.Client(
        base_url=base_url,
        headers=headers,
        timeout=TIMEOUT,
        follow_redirects=False,
        transport=TRANSPORT,
    ) as client:
        try:
            resp = client.request(method, path, params=params, json=json_body)
        except httpx.HTTPError as exc:
            raise ToolError(f"{site}: request failed: {exc}") from exc
    if resp.status_code >= 300:
        raise ToolError(f"{site}: HTTP {resp.status_code}: {_error_text(resp)}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise ToolError(f"{site}: non-JSON response") from exc
    if isinstance(body, dict):
        if "data" in body:
            return body["data"]
        if "message" in body:
            return body["message"]
    return body


def _q(segment: str) -> str:
    if not segment or not segment.strip():
        raise ToolError("doctype/name must not be empty")
    return quote(segment, safe="")


def get_doctype_meta(doctype: str, site: str = "test") -> dict:
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
    site: str = "test",
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


def get_doc(doctype: str, name: str, site: str = "test") -> dict:
    """Fetch one document with its child tables."""
    return _request(site, "GET", f"/api/resource/{_q(doctype)}/{_q(name)}")


def run_report(report_name: str, filters: dict | None = None, site: str = "test") -> dict:
    """Run a query/script report; returns columns and result rows."""
    return _request(
        site,
        "POST",
        "/api/method/frappe.desk.query_report.run",
        json_body={"report_name": report_name, "filters": filters or {}},
    )


def call_method(
    method: str, args: dict | None = None, http_method: str = "POST", site: str = "test"
) -> Any:
    """Call a whitelisted server method (dotted path). Delete/cancel/rename-type methods are refused."""
    if not _METHOD_NAME.match(method):
        raise ToolError(f"invalid method name '{method}'")
    if _DENY_METHOD.search(method):
        raise ToolError(f"method '{method}' is refused: destructive methods are not allowed")
    http_method = http_method.upper()
    if http_method not in ("GET", "POST"):
        raise ToolError("http_method must be GET or POST")
    path = f"/api/method/{method}"
    if http_method == "GET":
        return _request(site, "GET", path, params=args or {})
    return _request(site, "POST", path, json_body=args or {})


def create_doc(doctype: str, data: dict, site: str = "test") -> dict:
    """Create a document (not submitted). data: field values, child tables as lists."""
    return _request(site, "POST", f"/api/resource/{_q(doctype)}", json_body=data)


def update_doc(doctype: str, name: str, data: dict, site: str = "test") -> dict:
    """Update fields of an existing document."""
    return _request(site, "PUT", f"/api/resource/{_q(doctype)}/{_q(name)}", json_body=data)


for _fn in (get_doctype_meta, list_docs, get_doc, run_report, call_method, create_doc, update_doc):
    mcp.tool(_fn)


def main() -> None:
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
