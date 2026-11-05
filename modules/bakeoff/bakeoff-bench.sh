# bakeoff-bench — throwaway Frappe benches for the bake-off (devhobby only).
#
#   bakeoff-bench init <n> [ROSTER_PATH]  clone ~/bakeoff/benches/_template -> bN (paths, ports,
#                                          apps: frappe erpnext hrms dominobyte_roster -> ROSTER_PATH,
#                                          default ~/bakeoff/repo), DB user + site bN.localhost from
#                                          the snapshot (or a fresh new-site + snapshot when none exists)
#   bakeoff-bench snapshot <n>            dump bN's site DB + files to ~/bakeoff/snapshots (the reset point)
#   bakeoff-bench reset <n>               stop, reload DB + files from the snapshot, start, wait for HTTP
#   bakeoff-bench link <n> <path> [--migrate]  point bN's dominobyte_roster at a checkout/worktree,
#                                          restart (--migrate: bench migrate for that tree's schema)
#   bakeoff-bench start|stop|restart <n>  the user unit bakeoff-bench@<n>
#   bakeoff-bench mail <n>                default outgoing Email Account -> local Mailpit 127.0.0.1:1026
#                                          (mute_emails off); run before `snapshot`
#   bakeoff-bench status                  benches, units, roster links
#   bakeoff-bench run <n> CMD...          run CMD in bN's directory with the ERPNext dev toolchain
#
# The template is a copy of devpro's benches/test code (frappe/erpnext/hrms + env + built assets;
# no site, no data). Sites are FRESH installs (no devpro data): Administrator / test123, emails
# muted, allow_tests on, ERPNext setup completed by hrms.tests.test_utils.before_tests (the HRMS test hook).
# DB admin work goes through the local MariaDB root socket (sudo -n mariadb).
set -euo pipefail
ROOT=$HOME/bakeoff
BENCHES=$ROOT/benches
TEMPLATE=$BENCHES/_template
SNAP=$ROOT/snapshots
WS=$ROOT/erpnext
SRC_PREFIX=/home/cristian/development/erpnext/benches/test/
ADMIN_PW=test123

die() { echo "bakeoff-bench: $*" >&2; exit 1; }
need_n() {
  [[ "${1:-}" =~ ^[1-9]$ ]] || die "n must be 1..9"
  N=$1; NAME=b$N; DIR=$BENCHES/$NAME; SITE=$NAME.localhost
  WEB=80${N}0; SOCK=90${N}0; RCACHE=130${N}0; RQUEUE=110${N}0
}
devenv() {
  # Cached `nix print-dev-env` of the ERPNext workspace flake (python 3.14, mariadb client,
  # node, LD_LIBRARY_PATH). Regenerated when flake.nix/flake.lock change.
  local cache=$WS/.dev-env.sh
  if [ ! -s "$cache" ] || [ "$WS/flake.lock" -nt "$cache" ] || [ "$WS/flake.nix" -nt "$cache" ]; then
    nix print-dev-env "path:$WS" > "$cache.tmp" && mv "$cache.tmp" "$cache"
  fi
  set +u
  # shellcheck disable=SC1090
  source "$cache"
  set -u
  export PATH="@benchBin@:$PATH"
}
inb() { ( cd "$DIR" && devenv && "$@" ); }
bench_() { inb bench "$@"; }
root_sql() { sudo -n @mariadb@/bin/mariadb -uroot -e "$1"; }
site_cfg() { python3 -c "import json,sys;print(json.load(open(sys.argv[1]))[sys.argv[2]])" "$1" "$2"; }
unit() { systemctl --user "$1" "bakeoff-bench@$N.service"; }

warm() {
  # Warm Redis like a lived-in bench: some reference tests patch frappe.db.get_value globally,
  # so a cold client_cache (Language doc for date formats, read by every Jinja render) breaks
  # them (E1, 11-05). One render + a desk ping fills those keys.
  bench_ --site "$SITE" execute frappe.render_template --args "['{{ frappe.format_date(\"2026-01-01\") }}', {}]" >/dev/null 2>&1 || echo "WARN: cache warm-up failed" >&2
  curl -s -o /dev/null -H "Host: $SITE" "http://127.0.0.1:$WEB/login" || true
}

wait_http() {
  local _
  for _ in $(seq 1 90); do
    if curl -sf -o /dev/null -H "Host: $SITE" "http://127.0.0.1:$WEB/api/method/ping"; then return 0; fi
    sleep 1
  done
  die "$SITE did not answer on :$WEB within 90 s"
}

create_db() {   # (re)create the site DB + user from site_config.json
  local cfg=$DIR/sites/$SITE/site_config.json db user pw
  db=$(site_cfg "$cfg" db_name); user=$(site_cfg "$cfg" db_user); pw=$(site_cfg "$cfg" db_password)
  root_sql "DROP DATABASE IF EXISTS \`$db\`; CREATE DATABASE \`$db\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
    CREATE USER IF NOT EXISTS '$user'@'localhost' IDENTIFIED BY '$pw'; ALTER USER '$user'@'localhost' IDENTIFIED BY '$pw';
    CREATE USER IF NOT EXISTS '$user'@'127.0.0.1' IDENTIFIED BY '$pw'; ALTER USER '$user'@'127.0.0.1' IDENTIFIED BY '$pw';
    GRANT ALL PRIVILEGES ON \`$db\`.* TO '$user'@'localhost'; GRANT ALL PRIVILEGES ON \`$db\`.* TO '$user'@'127.0.0.1'; FLUSH PRIVILEGES;"
}

load_snapshot() {
  local cfg=$DIR/sites/$SITE/site_config.json db user pw
  db=$(site_cfg "$cfg" db_name); user=$(site_cfg "$cfg" db_user); pw=$(site_cfg "$cfg" db_password)
  create_db
  gunzip -c "$SNAP/site.sql.gz" | MYSQL_PWD="$pw" @mariadb@/bin/mariadb -h127.0.0.1 -u"$user" "$db"
  rm -rf "$DIR/sites/$SITE/public" "$DIR/sites/$SITE/private"
  tar -xzf "$SNAP/site-files.tar.gz" -C "$DIR/sites/$SITE"
  mkdir -p "$DIR/sites/$SITE/private/backups" "$DIR/sites/$SITE/logs" "$DIR/sites/$SITE/locks"
}

write_site_config() {
  mkdir -p "$DIR/sites/$SITE"
  python3 - "$DIR/sites/$SITE/site_config.json" "$NAME" "$SITE" "$WEB" "$SNAP/encryption_key" <<'PY'
import json, os, secrets, sys
dst, name, site, web, keyf = sys.argv[1:]
c = {"db_name": f"_{name}_{secrets.token_hex(4)}", "db_password": secrets.token_urlsafe(18),
     "db_type": "mariadb", "host_name": f"http://{site}:{web}", "allow_tests": True,
     "mute_emails": 0, "developer_mode": 1}
c["db_user"] = c["db_name"]
if os.path.exists(keyf):   # same key as the snapshot so stored secrets stay readable
    c["encryption_key"] = open(keyf).read().strip()
with open(dst, "w") as fh:
    json.dump(c, fh, indent=1, sort_keys=True)
os.chmod(dst, 0o600)
PY
}

cmd_init() {
  need_n "${1:-}"; local roster=${2:-$ROOT/repo}
  [ -d "$DIR" ] && die "$DIR exists"
  [ -d "$TEMPLATE/apps/frappe" ] || die "no template at $TEMPLATE"
  [ -d "$roster/dominobyte_roster" ] || die "$roster is not a dominobyte_roster checkout"
  echo "=== copying template -> $NAME $(date +%T)"
  cp -a "$TEMPLATE" "$DIR"
  rm -rf "$DIR"/apps/domino_billing "$DIR"/apps/domino_finance "$DIR"/sites/assets/domino_* "$DIR"/logs/* "$DIR"/config/pids/*
  ( cd "$DIR"/env/lib/python3*/site-packages && rm -rf domino_billing-* domino_finance-* domino_finance.pth \
      __editable___domino_billing_* __editable__.domino_billing-* )
  grep -rIl "$SRC_PREFIX" "$DIR/env" "$DIR/config" "$DIR/sites/common_site_config.json" "$DIR/Procfile" 2>/dev/null \
    | xargs -r sed -i "s#$SRC_PREFIX#$DIR/#g"
  for a in frappe erpnext hrms; do ln -sfn "$DIR/apps/$a/$a/public" "$DIR/sites/assets/$a"; done
  ln -sfn "$roster" "$DIR/apps/dominobyte_roster"
  ln -sfn "$roster/dominobyte_roster/public" "$DIR/sites/assets/dominobyte_roster"
  printf 'frappe\nerpnext\nhrms\ndominobyte_roster\n' > "$DIR/sites/apps.txt"
  python3 - "$DIR/sites/common_site_config.json" "$SITE" "$WEB" "$SOCK" "$RCACHE" "$RQUEUE" <<'PY'
import json, sys
p, site, web, sock, rc, rq = sys.argv[1:]
c = json.load(open(p))
for k in ("chromium_path", "chromium_start_timeout"):
    c.pop(k, None)
c.update({"default_site": site, "webserver_port": int(web), "socketio_port": int(sock),
          "redis_cache": f"redis://127.0.0.1:{rc}", "redis_socketio": f"redis://127.0.0.1:{rc}",
          "redis_queue": f"redis://127.0.0.1:{rq}", "gunicorn_workers": 4, "background_workers": 1,
          "live_reload": False, "developer_mode": 1, "serve_default_site": True})
json.dump(c, open(p, "w"), indent=1, sort_keys=True)
PY
  sed -i "s/^port [0-9]*$/port $RCACHE/" "$DIR/config/redis_cache.conf"
  sed -i "s/^port [0-9]*$/port $RQUEUE/" "$DIR/config/redis_queue.conf"
  # Lean Procfile: no file watcher, socketio or scheduler (tests and the MCP need neither).
  cat > "$DIR/Procfile" <<EOF
redis_cache: redis-server config/redis_cache.conf
redis_queue: redis-server config/redis_queue.conf
web: bench serve --port $WEB
worker: bench worker 1>> logs/worker.log 2>> logs/worker.error.log
EOF
  mkdir -p "$DIR/logs" "$DIR/config/pids"
  if [ -s "$SNAP/site.sql.gz" ]; then
    write_site_config
    echo "=== loading snapshot into $SITE $(date +%T)"
    load_snapshot
    bench_ --site "$SITE" set-config host_name "http://$SITE:$WEB" >/dev/null
  else
    echo "=== fresh site $SITE (no snapshot yet) $(date +%T)"
    local apw; apw=$(head -c 18 /dev/urandom | base64 | tr -d '/+=')
    root_sql "CREATE USER IF NOT EXISTS 'bakeoff_admin'@'localhost' IDENTIFIED BY '$apw'; ALTER USER 'bakeoff_admin'@'localhost' IDENTIFIED BY '$apw';
      CREATE USER IF NOT EXISTS 'bakeoff_admin'@'127.0.0.1' IDENTIFIED BY '$apw'; ALTER USER 'bakeoff_admin'@'127.0.0.1' IDENTIFIED BY '$apw';
      GRANT ALL PRIVILEGES ON *.* TO 'bakeoff_admin'@'localhost' WITH GRANT OPTION; GRANT ALL PRIVILEGES ON *.* TO 'bakeoff_admin'@'127.0.0.1' WITH GRANT OPTION; FLUSH PRIVILEGES;"
    ( cd "$DIR" && devenv && redis-server config/redis_cache.conf --daemonize yes >/dev/null && redis-server config/redis_queue.conf --daemonize yes >/dev/null
      bench new-site "$SITE" --db-host 127.0.0.1 --db-root-username bakeoff_admin --db-root-password "$apw" \
        --admin-password "$ADMIN_PW" --install-app erpnext --install-app hrms --install-app dominobyte_roster
      bench --site "$SITE" set-config allow_tests true; bench --site "$SITE" set-config mute_emails 1
      bench --site "$SITE" set-config developer_mode 1
      # The HRMS test hook's setup wizard uses password "test" (rejected by the password policy).
      bench --site "$SITE" execute frappe.db.set_single_value --args "['System Settings', 'enable_password_policy', 0]"
      # Its set_defaults() trips roster's month lock ("Month May 2013 is closed") after the
      # wizard has completed; the roster suite builds its own fixtures, so that is tolerated.
      bench --site "$SITE" execute hrms.tests.test_utils.before_tests || echo "WARN: before_tests partial (wizard done: $(bench --site "$SITE" execute frappe.is_setup_complete 2>/dev/null | tail -1))"
      redis-cli -p "$RCACHE" shutdown nosave >/dev/null 2>&1 || true; redis-cli -p "$RQUEUE" shutdown nosave >/dev/null 2>&1 || true )
    root_sql "DROP USER IF EXISTS 'bakeoff_admin'@'localhost'; DROP USER IF EXISTS 'bakeoff_admin'@'127.0.0.1';"
    cmd_snapshot "$N"
  fi
  unit start; wait_http
  echo "CREATED $NAME -> http://$SITE:$WEB (roster: $roster)"
}

cmd_snapshot() {
  need_n "${1:-}"; mkdir -p "$SNAP"
  local cfg=$DIR/sites/$SITE/site_config.json db user pw
  # Frappe writes encryption_key lazily; pin it now so every bench shares it with the dump.
  grep -q '"encryption_key"' "$cfg" || bench_ --site "$SITE" execute frappe.utils.password.get_encryption_key >/dev/null
  db=$(site_cfg "$cfg" db_name); user=$(site_cfg "$cfg" db_user); pw=$(site_cfg "$cfg" db_password)
  MYSQL_PWD="$pw" @mariadb@/bin/mariadb-dump -h127.0.0.1 -u"$user" --single-transaction --quick \
    --routines --events "$db" | gzip -1 > "$SNAP/site.sql.gz.tmp"
  mv "$SNAP/site.sql.gz.tmp" "$SNAP/site.sql.gz"
  ( cd "$DIR/sites/$SITE" && mkdir -p public private/files && tar -czf "$SNAP/site-files.tar.gz" public private/files )
  site_cfg "$cfg" encryption_key > "$SNAP/encryption_key"; chmod 600 "$SNAP/encryption_key"
  ( cd "$DIR/apps/dominobyte_roster" && git rev-parse HEAD > "$SNAP/roster-commit" ) || true
  echo "snapshot: $SNAP ($(du -h "$SNAP/site.sql.gz" | cut -f1) sql; roster $(cat "$SNAP/roster-commit" 2>/dev/null))"
}

cmd_mail() {
  need_n "${1:-}"
  bench_ --site "$SITE" set-config mute_emails 0 >/dev/null
  cat > "$DIR/sites/bakeoff_mail_setup.py" <<'PY'
import frappe
def run():
    name = "Bakeoff Outgoing"
    d = frappe.get_doc("Email Account", name) if frappe.db.exists("Email Account", name) else frappe.new_doc("Email Account")
    d.update({"email_account_name": name, "email_id": "bakeoff@example.test", "service": "",
              "enable_outgoing": 1, "default_outgoing": 1, "enable_incoming": 0,
              "smtp_server": "127.0.0.1", "smtp_port": 1026, "use_tls": 0, "use_ssl_for_outgoing": 0,
              "use_starttls": 0, "no_smtp_authentication": 1, "awaiting_password": 0})
    d.flags.ignore_validate = False
    d.save(ignore_permissions=True)
    # Frappe drops mail to the stock admin@example.com address (QueueBuilder returns []), which
    # makes the roster's mail-intent tests fail on a fresh site (H5, 11-05). Lived-in benches
    # have a real Administrator address.
    frappe.db.set_value("User", "Administrator", "email", "administrator@bakeoff.example.test")
    frappe.db.commit()
    print("email account:", d.name)
PY
  ( cd "$DIR/sites" && devenv && "$DIR/env/bin/python" -c "import sys; sys.path.insert(0, '.'); import frappe; frappe.init('$SITE'); frappe.connect(); import bakeoff_mail_setup as m; m.run(); frappe.destroy()" )
  rm -f "$DIR/sites/bakeoff_mail_setup.py"
}

cmd_reset() {
  need_n "${1:-}"; [ -d "$DIR" ] || die "$NAME not created"; [ -s "$SNAP/site.sql.gz" ] || die "no snapshot"
  local t0; t0=$(date +%s)
  unit stop || true
  if [ ! -d "$DIR/apps/dominobyte_roster/dominobyte_roster" ]; then   # linked tree is gone
    ln -sfn "$ROOT/repo" "$DIR/apps/dominobyte_roster"
    ln -sfn "$ROOT/repo/dominobyte_roster/public" "$DIR/sites/assets/dominobyte_roster"
    echo "$NAME: roster link was dangling -> reset to $ROOT/repo"
  fi
  load_snapshot
  unit start; wait_http; warm
  echo "RESET $NAME in $(( $(date +%s) - t0 )) s"
}

cmd_link() {
  need_n "${1:-}"; local path=${2:?path} migrate=${3:-}
  [ -d "$path/dominobyte_roster" ] || die "$path is not a dominobyte_roster checkout"
  ln -sfn "$(realpath "$path")" "$DIR/apps/dominobyte_roster"
  ln -sfn "$(realpath "$path")/dominobyte_roster/public" "$DIR/sites/assets/dominobyte_roster"
  if [ "$migrate" = --migrate ]; then
    # Roster patches (e.g. the first PCO deployment) refuse to run outside maintenance mode.
    local mlog=$DIR/logs/migrate-last.log mrc=0
    bench_ --site "$SITE" set-maintenance-mode on >/dev/null 2>&1 || true
    bench_ --site "$SITE" migrate > "$mlog" 2>&1 || mrc=$?
    bench_ --site "$SITE" set-maintenance-mode off >/dev/null 2>&1 || true
    bench_ --site "$SITE" set-config maintenance_mode 0 >/dev/null 2>&1 || true
    if [ "$mrc" != 0 ]; then
      tr '\r' '\n' < "$mlog" | grep -aiE "error|exception" | tail -3 >&2
      echo "bakeoff-bench: migrate FAILED (rc $mrc, log $mlog)" >&2
    fi
  fi
  unit restart; wait_http; warm
  echo "$NAME: dominobyte_roster -> $path ($(git -C "$path" log --oneline -1 2>/dev/null | cut -c1-60))"
}

cmd_status() {
  for d in "$BENCHES"/b[1-9]; do
    [ -d "$d" ] || continue
    need_n "${d##*/b}"
    echo "$NAME: $(systemctl --user is-active "bakeoff-bench@$N.service" 2>/dev/null) http://$SITE:$WEB roster -> $(readlink "$DIR/apps/dominobyte_roster")"
  done
  [ -s "$SNAP/site.sql.gz" ] && echo "snapshot: $(stat -c %y "$SNAP/site.sql.gz" | cut -d. -f1), roster $(cat "$SNAP/roster-commit" 2>/dev/null)"
  true
}

case "${1:-}" in
  init) shift; cmd_init "$@" ;;
  snapshot) shift; cmd_snapshot "$@" ;;
  reset) shift; cmd_reset "$@" ;;
  link) shift; cmd_link "$@" ;;
  start|stop|restart) need_n "${2:-}"; unit "$1" ;;
  mail) shift; cmd_mail "$@" ;;
  status) cmd_status ;;
  run) shift; need_n "${1:-}"; shift; inb "$@" ;;
  devenv) devenv; shift; exec "$@" ;;
  *) echo "usage: bakeoff-bench init|snapshot|reset|link|start|stop|restart|status|run (see modules/bakeoff/bakeoff-bench.sh)" >&2; exit 2 ;;
esac
