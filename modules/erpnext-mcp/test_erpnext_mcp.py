import json
import re

import httpx
import pytest
from fastmcp.exceptions import ToolError

import erpnext_mcp as m


@pytest.fixture
def calls(monkeypatch):
    """Mock both benches; record every request."""
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
    for site in ("TEST", "UAT"):
        monkeypatch.setenv(f"ERPNEXT_{site}_KEY", f"k-{site}")
        monkeypatch.setenv(f"ERPNEXT_{site}_SECRET", f"s-{site}")
    return seen


def test_allowlist_only_two_local_benches():
    assert m.SITES == {
        "test": ("http://127.0.0.1:8000", "test.localhost"),
        "uat": ("http://127.0.0.1:8001", "uat.localhost"),
    }


def test_unknown_site_refused(calls):
    with pytest.raises(ToolError, match="unknown site"):
        m.get_doc("Customer", "X", site="prod")
    with pytest.raises(ToolError, match="unknown site"):
        m.get_doc("Customer", "X", site="http://evil.example")
    assert calls == []


def test_no_credentials_clear_error(monkeypatch, tmp_path):
    for v in ("ERPNEXT_TEST_KEY", "ERPNEXT_TEST_SECRET"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("ERPNEXT_MCP_ENV_FILE", str(tmp_path / "missing.env"))
    with pytest.raises(ToolError, match="no credentials for site 'test'"):
        m.get_doc("Customer", "X")


def test_credentials_from_env_file(monkeypatch, tmp_path, calls):
    for v in ("ERPNEXT_UAT_KEY", "ERPNEXT_UAT_SECRET"):
        monkeypatch.delenv(v, raising=False)
    f = tmp_path / "e.env"
    f.write_text("# comment\nERPNEXT_UAT_KEY=fk\nERPNEXT_UAT_SECRET='fs'\n")
    monkeypatch.setenv("ERPNEXT_MCP_ENV_FILE", str(f))
    m.get_doc("Customer", "X", site="uat")
    assert calls[-1].headers["authorization"] == "token fk:fs"


def test_request_shape_auth_host_and_url(calls):
    m.get_doc("Sales Invoice", "ACC/SINV/0001", site="uat")
    req = calls[-1]
    assert str(req.url).startswith("http://127.0.0.1:8001/")
    assert req.headers["host"] == "uat.localhost"
    assert req.headers["authorization"] == "token k-UAT:s-UAT"
    # slashes in names are encoded, so a name cannot walk to another path
    assert req.url.raw_path.decode() == "/api/resource/Sales%20Invoice/ACC%2FSINV%2F0001"


def test_list_docs_params(calls):
    m.list_docs("Customer", filters=[["disabled", "=", 0]], fields=["name", "customer_name"], limit=9999)
    req = calls[-1]
    assert req.method == "GET"
    assert json.loads(req.url.params["fields"]) == ["name", "customer_name"]
    assert json.loads(req.url.params["filters"]) == [["disabled", "=", 0]]
    assert req.url.params["limit_page_length"] == str(m.MAX_LIMIT)


def test_doctype_meta_drops_layout_fields(calls):
    meta = m.get_doctype_meta("Customer")
    assert meta["name"] == "Customer"
    assert [f["fieldname"] for f in meta["fields"]] == ["customer_name"]


def test_create_and_update_methods(calls):
    m.create_doc("ToDo", {"description": "x"})
    assert calls[-1].method == "POST"
    assert json.loads(calls[-1].content) == {"description": "x"}
    m.update_doc("ToDo", "abc", {"status": "Closed"})
    assert calls[-1].method == "PUT"
    assert calls[-1].url.path == "/api/resource/ToDo/abc"


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
            m.call_method(meth)
    assert not any(c.method == "DELETE" for c in calls)
    assert not hasattr(m, "delete_doc")


def test_call_method_allowed_and_validated(calls):
    assert m.call_method("frappe.client.get_count", {"doctype": "ToDo"}) == {"ok": True}
    assert calls[-1].url.path == "/api/method/frappe.client.get_count"
    with pytest.raises(ToolError, match="invalid method name"):
        m.call_method("../../etc/passwd")
    with pytest.raises(ToolError, match="GET or POST"):
        m.call_method("frappe.client.get_count", http_method="DELETE")


def test_only_api_paths(calls):
    with pytest.raises(ToolError, match="/api/"):
        m._request("test", "GET", "//evil.example/x")


def test_server_error_is_readable(calls):
    with pytest.raises(ToolError, match=re.escape("HTTP 417: ValidationError | bad value")):
        m.call_method("boom.fail")


def test_tools_registered():
    import asyncio

    names = {t.name for t in asyncio.run(m.mcp.list_tools())}
    assert names == {
        "get_doctype_meta",
        "list_docs",
        "get_doc",
        "run_report",
        "call_method",
        "create_doc",
        "update_doc",
    }
