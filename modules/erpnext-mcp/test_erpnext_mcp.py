import json
import re

import httpx
import pytest
from fastmcp.exceptions import ToolError

import erpnext_mcp as m


def make_bench(root, name, port, sites, default_site=None):
    sites_dir = root / name / "sites"
    sites_dir.mkdir(parents=True)
    cfg = {"default_site": default_site or (sites[0] if sites else None)}
    if port is not None:
        cfg["webserver_port"] = port
    (sites_dir / "common_site_config.json").write_text(json.dumps(cfg))
    (sites_dir / "assets").mkdir()
    for s in sites:
        (sites_dir / s).mkdir()
        (sites_dir / s / "site_config.json").write_text('{"db_password": "never-read"}')
    return root / name


@pytest.fixture
def benches(tmp_path, monkeypatch):
    root = tmp_path / "benches"
    root.mkdir()
    make_bench(root, "test", 8000, ["test.localhost"])
    make_bench(root, "test3", 8030, ["test3.localhost"])
    make_bench(
        root,
        "test4",
        8040,
        ["test4.localhost", "leave-rename-6eb9b147e2e4.localhost"],
        default_site="test4.localhost",
    )
    make_bench(root, "uat", 8001, ["uat.localhost"])
    make_bench(root, "pdf-chromium", None, [])  # no webserver_port: not served
    make_bench(root, "dupfix", None, ["dupfix.localhost"])  # sites but no port: skipped
    (root / "testenv.sh").write_text("#!/bin/sh\n")  # stray file, ignored
    monkeypatch.setenv("ERPNEXT_MCP_BENCHES", str(root))
    monkeypatch.delenv("ERPNEXT_MCP_ENV_MAP", raising=False)
    monkeypatch.delenv("ERPNEXT_MCP_READONLY_BENCHES", raising=False)
    creds = {
        s: {"key": f"k-{s}", "secret": f"s-{s}"}
        for s in ("test.localhost", "test3.localhost", "test4.localhost",
                  "leave-rename-6eb9b147e2e4.localhost", "uat.localhost")
    }
    cred_file = tmp_path / "credentials.json"
    cred_file.write_text(json.dumps(creds))
    monkeypatch.setenv("ERPNEXT_MCP_CREDENTIALS", str(cred_file))
    monkeypatch.chdir(tmp_path)  # neutral cwd: no default site
    return root


@pytest.fixture
def calls(benches, monkeypatch):
    """Mock every bench; record every request."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("getdoctype"):
            return httpx.Response(
                200,
                json={
                    "docs": [
                        {
                            "name": "Customer",
                            "module": "Selling",
                            "fields": [
                                {"fieldname": "customer_name", "fieldtype": "Data", "reqd": 1},
                                {"fieldname": "sb1", "fieldtype": "Section Break"},
                            ],
                        }
                    ]
                },
            )
        if path.startswith("/api/resource/"):
            return httpx.Response(200, json={"data": {"path": path, "method": request.method}})
        if path == "/api/method/boom.fail":
            return httpx.Response(
                417,
                json={
                    "exc_type": "ValidationError",
                    "_server_messages": json.dumps([json.dumps({"message": "bad value"})]),
                },
            )
        return httpx.Response(200, json={"message": {"ok": True}})

    monkeypatch.setattr(m, "TRANSPORT", httpx.MockTransport(handler))
    return seen


# ── discovery / environments ─────────────────────────────────────────────────


def test_discovery_skips_unserved_benches(benches):
    env = m.discover()
    assert set(env["benches"]) == {"test", "test3", "test4", "uat"}
    assert env["benches"]["test4"]["port"] == 8040
    assert env["benches"]["test4"]["sites"] == [
        "leave-rename-6eb9b147e2e4.localhost",
        "test4.localhost",
    ]
    # bench serve is pinned to default_site: only that one answers over HTTP
    assert env["benches"]["test4"]["served"] == ["test4.localhost"]


def test_list_environments(benches):
    envs = m.list_environments()
    by_site = {e["site"]: e for e in envs}
    assert set(by_site) == {
        "test.localhost",
        "test3.localhost",
        "test4.localhost",
        "leave-rename-6eb9b147e2e4.localhost",
        "uat.localhost",
    }
    assert by_site["uat.localhost"] == {
        "bench": "uat", "site": "uat.localhost", "port": 8001, "served": True,
        "credentials": True, "read_only": True, "default": False,
    }
    assert by_site["test3.localhost"]["read_only"] is False
    assert by_site["leave-rename-6eb9b147e2e4.localhost"]["served"] is False
    assert not any(e["default"] for e in envs)


def test_credentials_flag_false_when_missing(benches, tmp_path, monkeypatch):
    monkeypatch.setenv("ERPNEXT_MCP_CREDENTIALS", str(tmp_path / "missing.json"))
    assert not any(e["credentials"] for e in m.list_environments())


def test_env_map_file_used_instead_of_scan(benches, tmp_path, monkeypatch):
    mp = tmp_path / "map.json"
    mp.write_text(json.dumps({"root": str(benches), "benches": {
        "test5": {"port": 8050, "sites": ["test5.localhost"]},
        "evil": {"port": 22, "sites": ["x.localhost"]},          # privileged port: dropped
        "bad": {"port": 8099, "sites": ["http://evil.example"]},  # not a hostname: dropped
    }}))
    monkeypatch.setenv("ERPNEXT_MCP_ENV_MAP", str(mp))
    assert [e["site"] for e in m.list_environments()] == ["test5.localhost"]
    assert m.resolve_site("test5") == ("test5.localhost", "test5", 8050)


# ── site resolution / defaults ───────────────────────────────────────────────


def test_alias_resolution(benches):
    assert m.resolve_site("test3") == ("test3.localhost", "test3", 8030)
    assert m.resolve_site("test3.localhost") == ("test3.localhost", "test3", 8030)
    assert m.resolve_site("TEST4") == ("test4.localhost", "test4", 8040)
    assert m.resolve_site("uat")[2] == 8001


def test_unserved_site_refused_clearly(calls):
    with pytest.raises(ToolError, match="not served over HTTP.*pinned to its default_site test4.localhost"):
        m.get_doc("ToDo", "x", site="leave-rename-6eb9b147e2e4")
    assert calls == []


def test_bench_without_default_site_routes_by_host(tmp_path, monkeypatch, calls):
    root = tmp_path / "benches2"
    b = make_bench(root, "multi", 8070, ["a.localhost", "b.localhost"])
    cfg = b / "sites" / "common_site_config.json"
    cfg.write_text(json.dumps({"webserver_port": 8070}))  # no default_site: Host-routed
    monkeypatch.setenv("ERPNEXT_MCP_BENCHES", str(root))
    creds = tmp_path / "c2.json"
    creds.write_text(json.dumps({"b.localhost": {"key": "kb", "secret": "sb"}}))
    monkeypatch.setenv("ERPNEXT_MCP_CREDENTIALS", str(creds))
    m.get_doc("ToDo", "x", site="b")
    assert calls[-1].headers["host"] == "b.localhost"
    assert str(calls[-1].url).startswith("http://127.0.0.1:8070/")
    monkeypatch.chdir(b)
    with pytest.raises(ToolError, match="serves several sites"):
        m.get_doc("ToDo", "x")


def test_cwd_default_bench(benches, monkeypatch, calls):
    sub = benches / "test3" / "sites"
    monkeypatch.chdir(sub)
    assert m.resolve_site(None) == ("test3.localhost", "test3", 8030)
    m.get_doc("ToDo", "x")
    assert str(calls[-1].url).startswith("http://127.0.0.1:8030/")
    assert calls[-1].headers["host"] == "test3.localhost"
    assert [e["site"] for e in m.list_environments() if e["default"]] == ["test3.localhost"]


def test_cwd_default_multisite_bench_uses_served_site(benches, monkeypatch):
    monkeypatch.chdir(benches / "test4")
    assert m.resolve_site(None)[0] == "test4.localhost"
    monkeypatch.chdir(benches / "test4" / "sites" / "test4.localhost")
    assert m.resolve_site(None)[0] == "test4.localhost"


def test_cwd_in_unserved_site_dir_errors(benches, monkeypatch, calls):
    monkeypatch.chdir(benches / "test4" / "sites" / "leave-rename-6eb9b147e2e4.localhost")
    with pytest.raises(ToolError, match="not served over HTTP"):
        m.get_doc("ToDo", "x")
    assert calls == []


def test_no_default_outside_benches(benches, calls):
    with pytest.raises(ToolError, match="no `site` given.*not inside a bench"):
        m.list_docs("ToDo")
    assert calls == []


def test_unserved_bench_cwd_no_default(benches, monkeypatch, calls):
    monkeypatch.chdir(benches / "pdf-chromium")
    with pytest.raises(ToolError, match="not inside a served bench"):
        m.get_doc("ToDo", "x")


# ── allowlist ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [
    "prod", "http://evil.example", "erp.dominobyte.com", "127.0.0.1:8000",
    "dupfix", "dupfix.localhost", "localhost", "test4.localhost:9999",
])
def test_unknown_site_refused(calls, bad):
    with pytest.raises(ToolError, match="unknown site"):
        m.get_doc("Customer", "X", site=bad)
    assert calls == []


def test_request_shape_auth_host_and_url(calls):
    m.get_doc("Sales Invoice", "ACC/SINV/0001", site="test4")
    req = calls[-1]
    assert str(req.url).startswith("http://127.0.0.1:8040/")
    assert req.headers["host"] == "test4.localhost"
    assert req.headers["authorization"] == "token k-test4.localhost:s-test4.localhost"
    # slashes in names are encoded, so a name cannot walk to another path
    assert req.url.raw_path.decode() == "/api/resource/Sales%20Invoice/ACC%2FSINV%2F0001"


def test_no_credentials_clear_error(benches, tmp_path, monkeypatch):
    monkeypatch.setenv("ERPNEXT_MCP_CREDENTIALS", str(tmp_path / "missing.json"))
    with pytest.raises(ToolError, match="no credentials for site 'test.localhost'.*erpnext-mcp-provision"):
        m.get_doc("Customer", "X", site="test")


# ── tools ────────────────────────────────────────────────────────────────────


def test_list_docs_params(calls):
    m.list_docs("Customer", filters=[["disabled", "=", 0]], fields=["name", "customer_name"],
                limit=9999, site="test")
    req = calls[-1]
    assert req.method == "GET"
    assert json.loads(req.url.params["fields"]) == ["name", "customer_name"]
    assert json.loads(req.url.params["filters"]) == [["disabled", "=", 0]]
    assert req.url.params["limit_page_length"] == str(m.MAX_LIMIT)


def test_doctype_meta_drops_layout_fields(calls):
    meta = m.get_doctype_meta("Customer", site="test")
    assert meta["name"] == "Customer"
    assert [f["fieldname"] for f in meta["fields"]] == ["customer_name"]


def test_create_and_update_methods(calls):
    m.create_doc("ToDo", {"description": "x"}, site="test3")
    assert calls[-1].method == "POST"
    assert json.loads(calls[-1].content) == {"description": "x"}
    m.update_doc("ToDo", "abc", {"status": "Closed"}, site="test3")
    assert calls[-1].method == "PUT"
    assert calls[-1].url.path == "/api/resource/ToDo/abc"


def test_readonly_bench_refuses_writes(calls):
    with pytest.raises(ToolError, match="read-only"):
        m.create_doc("ToDo", {"description": "x"}, site="uat")
    with pytest.raises(ToolError, match="read-only"):
        m.update_doc("ToDo", "abc", {"status": "Closed"}, site="uat")
    with pytest.raises(ToolError, match="read-only"):
        m.call_method("frappe.client.set_value", {}, site="uat")
    assert calls == []
    m.call_method("frappe.client.get_count", {"doctype": "ToDo"}, http_method="GET", site="uat")
    m.list_docs("Company", site="uat")
    m.run_report("General Ledger", site="uat")
    assert len(calls) == 3


def test_no_delete_anywhere(calls):
    with pytest.raises(ToolError, match="not allowed"):
        m._request("test", "DELETE", "/api/resource/ToDo/abc")
    for meth in (
        "frappe.client.delete",
        "frappe.desk.reportview.delete_items",
        "frappe.client.cancel",
        "frappe.model.rename_doc.rename_doc",
        "erpnext.foo.bulk_delete",
    ):
        with pytest.raises(ToolError, match="refused"):
            m.call_method(meth, site="test")
    assert not any(c.method == "DELETE" for c in calls)
    assert not hasattr(m, "delete_doc")


def test_call_method_allowed_and_validated(calls):
    assert m.call_method("frappe.client.get_count", {"doctype": "ToDo"}, site="test") == {"ok": True}
    assert calls[-1].url.path == "/api/method/frappe.client.get_count"
    with pytest.raises(ToolError, match="invalid method name"):
        m.call_method("../../etc/passwd", site="test")
    with pytest.raises(ToolError, match="GET or POST"):
        m.call_method("frappe.client.get_count", http_method="DELETE", site="test")


def test_only_api_paths(calls):
    with pytest.raises(ToolError, match="/api/"):
        m._request("test", "GET", "//evil.example/x")


def test_server_error_is_readable(calls):
    with pytest.raises(ToolError, match=re.escape("HTTP 417: ValidationError | bad value")):
        m.call_method("boom.fail", site="test")


def test_tools_registered():
    import asyncio

    names = {t.name for t in asyncio.run(m.mcp.list_tools())}
    assert names == {
        "list_environments",
        "get_doctype_meta",
        "list_docs",
        "get_doc",
        "run_report",
        "call_method",
        "create_doc",
        "update_doc",
    }
    assert not any("delete" in n for n in names)
