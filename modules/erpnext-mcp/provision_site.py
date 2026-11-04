"""Ensure the MCP API user on ONE site; run by erpnext-mcp-provision with the bench's
own python (direnv toolchain), cwd = <bench>/sites, like benches/mailpit_flush.py.

usage: provision_site.py <site> <full|read> <out.json>

Idempotent: the user is created only if missing, existing API keys are reused (the
secret is read back decrypted); keys are generated only when missing/unreadable.
The key/secret go to <out.json> (mode 0600), never to stdout.
"""

import json
import os
import sys

import frappe

USER = "mcp-dev@mcp.localhost"
FULL_ROLES = ["System Manager"]
# Read-oriented: standard "* User" roles (read on most masters/transactions, no
# System Manager / Administrator). The MCP additionally refuses writes on these benches.
READ_ROLES = [
    "Accounts User",
    "Sales User",
    "Purchase User",
    "Stock User",
    "HR User",
    "Projects User",
    "Prepared Report User",
]
NEVER_IN_READ = {"System Manager", "Administrator"}

site, mode, out = sys.argv[1:4]
assert mode in ("full", "read")
frappe.init(site)
frappe.connect()
frappe.set_user("Administrator")
try:
    created = False
    if not frappe.db.exists("User", USER):
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": USER,
                "first_name": "MCP Dev",
                "user_type": "System User",
                "send_welcome_email": 0,
                "enabled": 1,
            }
        )
        user.flags.no_welcome_mail = True
        user.insert(ignore_permissions=True)
        created = True
    user = frappe.get_doc("User", USER)
    user.enabled = 1
    user.user_type = "System User"

    wanted = FULL_ROLES if mode == "full" else READ_ROLES
    wanted = [r for r in wanted if frappe.db.exists("Role", r)]
    have = {r.role for r in user.roles}
    if mode == "read":
        user.set("roles", [r for r in user.roles if r.role not in NEVER_IN_READ])
    for role in wanted:
        if role not in have:
            user.append("roles", {"role": role})

    from frappe.utils.password import get_decrypted_password

    secret = None
    if user.api_key:
        secret = get_decrypted_password("User", USER, "api_secret", raise_exception=False)
    new_key = not (user.api_key and secret)
    if new_key:
        user.api_key = frappe.generate_hash(length=15)
        secret = frappe.generate_hash(length=15)
        user.api_secret = secret
    user.save(ignore_permissions=True)
    frappe.db.commit()
    roles = sorted(r.role for r in frappe.get_doc("User", USER).roles)

    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(
            {
                "user": USER,
                "key": user.api_key,
                "secret": secret,
                "roles": roles,
                "created": created,
                "new_key": new_key,
            },
            fh,
        )
finally:
    frappe.destroy()
