---
name: erpnext-bench
description: How to run Frappe/ERPNext bench commands on devpro safely - the devshell, which bench is which, backups before migrate, and what never to do on the shared uat bench. Use before any bench, migrate, build, backup or site command.
---

# ERPNext benches on devpro

## Always the devshell
`nix develop ~/development/erpnext --command bench <args>` (run from the bench
folder, e.g. `~/development/erpnext/benches/test`). Outside it the mariadb client,
yarn and Node 24 are missing: `bench backup` then says "Database or
site_config.json may be corrupted" and `bench setup requirements` fails on yarn/node.

## Which bench
- `benches/uat` (:8001, LAN-visible) is shared with colleagues: only read unless
  the user asked for that exact change.
- `benches/test*` are dev copies: use these to try migrations and patches.
- List what is served and its sites with the ERPNext MCP tool `list_environments`.
- `/srv/pi-envs/w1`…`w6` belong to the pi-task worker pool: never touch.

## Before a migrate or schema change
1. Back up the site: `bench --site <site> backup` (inside the devshell).
2. Run the migrate on a test bench first; check the log for patch errors.
3. Only then, and only if asked, on uat.

## Services
Benches run as user units (`erpnext-uat.service`, `erpnext-test.service`);
MariaDB and Mailpit are user quadlets (`erpnext-mariadb`, `erpnext-mailpit`,
127.0.0.1:3306 / :1026). systemctl is a host command: the gate sends it for review.
A 500 with `AttributeError: is_ajax` usually means MariaDB is not reachable.

## Base app updates
`~/development/erpnext/update-base-apps.sh <bench> <site> <svc> <port>` does
backup, fast-forward, requirements, migrate, build, restart. The frappe/erpnext/hrms
remote is named `upstream`, not `origin`.
