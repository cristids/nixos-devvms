{ pkgs, ... }:

# ERPNext UAT stack on devpro — ROOTLESS podman, run by the `cristian` user.
#
# Shape mirrors the working cdssrv02 stack (nixos_llm_srv
# modules/containers/erpnext.nix): mariadb + redis + web(gunicorn) + nginx +
# worker + scheduler, all frappe/erpnext:v16, state in named volumes. Mirroring
# matters — UAT should fail the way production fails, and that module's odd bits
# (shared assets volume, the extra-app symlink pass) are scars from real bugs.
#
# DELIBERATE DIFFERENCES from cdssrv02:
#
#   * ROOTLESS. cdssrv02 runs these as root via virtualisation.oci-containers;
#     here they are a user-level systemd unit set under `cristian` (subuid
#     100000). These VMs are agent jails — an LLM harness drives this stack, so
#     it should not be able to hand containers root on the guest. Rootless also
#     puts images/volumes on /home (400G) rather than the 50G /var.
#
#   * No host port below 1024 and no firewall holes beyond the UI: rootless
#     cannot bind privileged ports anyway.
#
#   * Ports are the cdssrv02 numbers + 100 (8170 UI, 3407 db, 6480 redis) so
#     that when both are reachable on the LAN nothing is ambiguous about which
#     ERPNext you are looking at.
#
# NOT started automatically: the stack is heavy (~3G of images, six containers)
# and this VM is also a general dev box. Bring it up deliberately with
#   erpnext-uat up      (pull, create volumes, start, create the site once)
#   erpnext-uat down / status / logs / bench <args>
#
# Secrets: passwords live in ~/.config/erpnext-uat/env on the VM (mode 0600),
# NOT in this repo. `erpnext-uat up` generates one with random passwords on
# first run and prints the admin password. Under $HOME rather than /var/lib
# because the whole stack is rootless — /var/lib is root-owned, and the rest of
# the stack's state (images, volumes) already lives on /home for the same
# reason.
let
  # Single source of truth for the compose-ish topology. Kept as a shell
  # function library rather than declarative oci-containers because the stack is
  # opt-in (see above) — declaring it would start it at every boot.
  erpnextUat = pkgs.writeShellApplication {
    name = "erpnext-uat";
    runtimeInputs = with pkgs; [ podman coreutils gnugrep curl jq ];
    text = ''
      IMAGE_ERP=docker.io/frappe/erpnext:v16
      IMAGE_DB=docker.io/mariadb:10.6
      IMAGE_REDIS=docker.io/redis:7-alpine
      SITE=uat.localhost
      ENVF="''${XDG_CONFIG_HOME:-$HOME/.config}/erpnext-uat/env"
      NET=erpnext-uat

      # Host-side ports (cdssrv02 numbers + 100 to keep the two unambiguous).
      P_UI=8170
      P_WEB=8169
      P_DB=3407
      P_REDIS=6480

      ensure_env() {
        if [ ! -f "$ENVF" ]; then
          echo "→ generating $ENVF with random passwords"
          umask 077
          mkdir -p "$(dirname "$ENVF")"
          {
            echo "MYSQL_ROOT_PASSWORD=$(head -c18 /dev/urandom | base64 | tr -d '/+=')"
            echo "ERPNEXT_ADMIN_PASSWORD=$(head -c18 /dev/urandom | base64 | tr -d '/+=')"
          } > "$ENVF"
          echo "  admin password: $(grep ERPNEXT_ADMIN_PASSWORD "$ENVF" | cut -d= -f2)"
        fi
      }

      envget() { grep "^$1=" "$ENVF" | cut -d= -f2-; }

      # Rootless podman has no host.containers.internal by default on a
      # user network; a shared named network lets the containers address each
      # other by name instead, which is simpler and needs no host-gateway.
      ensure_net() {
        podman network exists "$NET" || podman network create "$NET" >/dev/null
      }

      seed_sites_volume() {
        # common_site_config.json must exist BEFORE the web container starts, or
        # bench cannot find the db/redis. Written via a throwaway container so
        # the volume's ownership (uid 1000 inside = cristian's subuid range)
        # stays correct without any chown from the host side.
        podman volume exists erpnext-uat-sites || podman volume create erpnext-uat-sites >/dev/null
        # NB: no heredoc here. This shell body is nested inside a Nix indented
        # string, and Nix's dedent does not reliably land a heredoc terminator
        # at column 0 — an indented `CONF` is not a terminator, and bash dies
        # with "unexpected end of file". printf builds the JSON instead.
        podman run --rm -v erpnext-uat-sites:/sites "$IMAGE_ERP" bash -c '
          if [ ! -f /sites/common_site_config.json ]; then
            printf "%s\n" \
              "{" \
              "  \"db_host\": \"erpnext-uat-mariadb\"," \
              "  \"db_port\": 3306," \
              "  \"redis_cache\": \"redis://erpnext-uat-redis:6379/0\"," \
              "  \"redis_queue\": \"redis://erpnext-uat-redis:6379/1\"," \
              "  \"redis_socketio\": \"redis://erpnext-uat-redis:6379/2\"" \
              "}" > /sites/common_site_config.json
          fi
          [ -f /sites/apps.txt ] || printf "frappe\nerpnext\n" > /sites/apps.txt
        '
      }

      start_stack() {
        ensure_net
        for v in sites logs env apps assets mariadb; do
          podman volume exists "erpnext-uat-$v" || podman volume create "erpnext-uat-$v" >/dev/null
        done
        seed_sites_volume

        podman container exists erpnext-uat-mariadb || podman run -d \
          --name erpnext-uat-mariadb --network "$NET" \
          --env-file "$ENVF" -p "$P_DB:3306" \
          -v erpnext-uat-mariadb:/var/lib/mysql \
          "$IMAGE_DB" \
          --character-set-server=utf8mb4 \
          --collation-server=utf8mb4_unicode_ci \
          --skip-character-set-client-handshake \
          --skip-innodb-read-only-compressed >/dev/null

        podman container exists erpnext-uat-redis || podman run -d \
          --name erpnext-uat-redis --network "$NET" \
          -p "$P_REDIS:6379" "$IMAGE_REDIS" >/dev/null

        podman container exists erpnext-uat-web || podman run -d \
          --name erpnext-uat-web --network "$NET" \
          --env-file "$ENVF" -p "$P_WEB:8000" \
          -v erpnext-uat-sites:/home/frappe/frappe-bench/sites \
          -v erpnext-uat-logs:/home/frappe/frappe-bench/logs \
          -v erpnext-uat-env:/home/frappe/frappe-bench/env \
          -v erpnext-uat-apps:/home/frappe/frappe-bench/apps \
          -v erpnext-uat-assets:/home/frappe/frappe-bench/assets \
          "$IMAGE_ERP" >/dev/null

        podman container exists erpnext-uat-nginx || podman run -d \
          --name erpnext-uat-nginx --network "$NET" -p "$P_UI:8080" \
          -e BACKEND=erpnext-uat-web:8000 \
          -e SOCKETIO=erpnext-uat-web:9000 \
          -e "FRAPPE_SITE_NAME_HEADER=$SITE" \
          -v erpnext-uat-sites:/home/frappe/frappe-bench/sites \
          -v erpnext-uat-apps:/home/frappe/frappe-bench/apps \
          -v erpnext-uat-assets:/home/frappe/frappe-bench/assets \
          --entrypoint /usr/local/bin/nginx-entrypoint.sh \
          "$IMAGE_ERP" >/dev/null

        podman container exists erpnext-uat-worker || podman run -d \
          --name erpnext-uat-worker --network "$NET" \
          -v erpnext-uat-sites:/home/frappe/frappe-bench/sites \
          -v erpnext-uat-logs:/home/frappe/frappe-bench/logs \
          -v erpnext-uat-env:/home/frappe/frappe-bench/env \
          -v erpnext-uat-apps:/home/frappe/frappe-bench/apps \
          "$IMAGE_ERP" bench worker --queue default,short,long >/dev/null

        podman container exists erpnext-uat-scheduler || podman run -d \
          --name erpnext-uat-scheduler --network "$NET" \
          -v erpnext-uat-sites:/home/frappe/frappe-bench/sites \
          -v erpnext-uat-logs:/home/frappe/frappe-bench/logs \
          -v erpnext-uat-env:/home/frappe/frappe-bench/env \
          -v erpnext-uat-apps:/home/frappe/frappe-bench/apps \
          "$IMAGE_ERP" bench schedule >/dev/null
      }

      create_site() {
        if podman exec erpnext-uat-web test -f "sites/$SITE/site_config.json" 2>/dev/null; then
          echo "→ site $SITE already exists"
          return 0
        fi
        echo "→ waiting for MariaDB"
        for _ in $(seq 1 60); do
          if podman exec erpnext-uat-mariadb \
               mariadb -uroot -p"$(envget MYSQL_ROOT_PASSWORD)" -e "SELECT 1" >/dev/null 2>&1; then
            break
          fi
          sleep 3
        done
        echo "→ creating site $SITE (this takes a few minutes)"
        podman exec erpnext-uat-web bench new-site "$SITE" \
          --mariadb-root-password "$(envget MYSQL_ROOT_PASSWORD)" \
          --admin-password "$(envget ERPNEXT_ADMIN_PASSWORD)" \
          --install-app erpnext --set-default
        # Mail: everything the UAT site "sends" goes to the mailpit sink on
        # cdssrv02 (port 1026, NOT 1025 — 1025 there is Proton Bridge).
        podman exec erpnext-uat-web bench --site "$SITE" set-config -g mail_server 192.168.40.15
        podman exec erpnext-uat-web bench --site "$SITE" set-config -g mail_port 1026
        podman exec erpnext-uat-web bench --site "$SITE" set-config -g use_ssl 0
      }

      case "''${1:-}" in
        up)
          ensure_env
          echo "→ pulling images (first run downloads ~3G)"
          podman image exists "$IMAGE_ERP"   || podman pull "$IMAGE_ERP"
          podman image exists "$IMAGE_DB"    || podman pull "$IMAGE_DB"
          podman image exists "$IMAGE_REDIS" || podman pull "$IMAGE_REDIS"
          start_stack
          create_site
          echo
          echo "ERPNext UAT up:  http://devpro:$P_UI/"
          echo "  Administrator / $(envget ERPNEXT_ADMIN_PASSWORD)"
          echo "  mail → mailpit http://192.168.40.15:8025"
          ;;
        down)
          podman stop -t 10 erpnext-uat-nginx erpnext-uat-scheduler erpnext-uat-worker \
                            erpnext-uat-web erpnext-uat-redis erpnext-uat-mariadb 2>/dev/null || true
          podman rm erpnext-uat-nginx erpnext-uat-scheduler erpnext-uat-worker \
                    erpnext-uat-web erpnext-uat-redis erpnext-uat-mariadb 2>/dev/null || true
          echo "stack down (volumes kept — 'erpnext-uat destroy' wipes data)"
          ;;
        destroy)
          echo "This DELETES the UAT database and site. Ctrl-C within 5s to abort."
          sleep 5
          "$0" down
          podman volume rm erpnext-uat-sites erpnext-uat-logs erpnext-uat-env \
                           erpnext-uat-apps erpnext-uat-assets erpnext-uat-mariadb 2>/dev/null || true
          echo "volumes removed"
          ;;
        status)
          podman ps -a --filter name=erpnext-uat --format \
            "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
          ;;
        logs)  shift; podman logs "''${1:-erpnext-uat-web}" 2>&1 | tail -50 ;;
        bench) shift; podman exec erpnext-uat-web bench --site "$SITE" "$@" ;;
        *)
          cat <<EOF
      erpnext-uat — UAT ERPNext stack on devpro (rootless podman)

        up        pull images, start all six containers, create the site once
        down      stop+remove containers (volumes/data kept)
        destroy   down, then DELETE all volumes (wipes the UAT database)
        status    container states
        logs [c]  tail a container's log (default erpnext-uat-web)
        bench …   run bench against the UAT site, e.g. erpnext-uat bench migrate

      UI http://devpro:$P_UI/   mail → mailpit 192.168.40.15:1026
      EOF
          ;;
      esac
    '';
  };
in
{
  environment.systemPackages = with pkgs; [
    erpnextUat
    podman
    podman-compose
  ];

  # Rootless podman for `cristian` (subuid/subgid ranges come from the users
  # module). newuidmap/newgidmap need the setuid wrappers this option installs.
  virtualisation.podman = {
    enable = true;
    dockerCompat = true;
    defaultNetwork.settings.dns_enabled = true;
  };

  # Containers keep running when the user is not logged in — otherwise the
  # stack dies the moment an ssh session ends.
  users.users.cristian.linger = true;

  # UI (8170) and the web/db/redis host ports the stack publishes. The VM is on
  # the LAN via macvtap, so this is what makes the UAT site reachable from the
  # laptops.
  networking.firewall.allowedTCPPorts = [ 8170 8169 3407 6480 ];
}
