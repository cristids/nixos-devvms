{ pkgs, ... }:

# devpro — professional work. Will eventually hold SCOPED work credentials (e.g. an
# Azure DevOps key), so agents in it get driven with more care than in devhobby.
# Everything else: ../common.
{
  imports = [
    # UAT ERPNext (rootless podman, opt-in via `erpnext-uat up`). devpro only —
    # devhobby has no business running the company's ERP.
    ../../modules/erpnext-uat.nix
    # Read-only Proton mail (himalaya → host read-only IMAP proxy). devpro only.
    ../../modules/proton-mail.nix
  ];

  networking.hostName = "devpro";

  # ERPNext MCP for Claude/Codex/omp, test :8000 + UAT :8001 benches only
  # (../../modules/omp-dev-agent.nix). Creds: /var/lib/erpnext-mcp.env.
  ompDevAgent.erpnext.enable = true;

  # No Codex Remote Control on devpro (2026-10-01): it needs OpenAI's standalone
  # install under ~/.codex/packages, which shadowed the Nix codex on PATH. The
  # Nix codex (pinned in ../../modules/agents.nix) is the only one here.
  systemd.user.services.codex-app-server.enable = false;

  # Leaves rebuild test tracker (2026-11-03): the page Claude on devpro publishes test
  # slices to; the owner marks PASS/FAIL from a laptop. Runs straight from the checkout so
  # agent updates and marks land in its versioned data file. No auth → LAN-limited below.
  systemd.services.leave-tracker = let dir = "/home/cristian/development/erpnext/leave-tracker"; in {
    description = "Leaves rebuild test tracker (http://devpro:8046)";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    unitConfig.ConditionPathExists = "${dir}/server.py";
    serviceConfig = {
      User = "cristian";
      Group = "users";
      WorkingDirectory = dir;
      ExecStart = "${pkgs.python3}/bin/python3 -I ${dir}/server.py --data ${dir}/data/leave-rebuild-tracker-data.json --html ${dir}/static/index.html --bind 0.0.0.0 --port 8046";
      Restart = "on-failure";
      RestartSec = 3;
      NoNewPrivileges = true;
      PrivateTmp = true;
      ProtectSystem = "strict";
      ReadWritePaths = [ "${dir}/data" ];
    };
  };

  # ERPNext dev benches (`bench serve`): 8001 (uat), 8030 (test3), 8040 (test4) and mailpit 8025,
  # plus the leave tracker 8046, reachable only from Work (.15, laptops) and Servers (.40) — not the whole LAN.
  # 8001/8025 used to be hand-inserted iptables rules that a firewall reload drops.
  networking.firewall.extraCommands = ''
    for net in 192.168.15.0/24 192.168.40.0/24; do
      for port in 8001 8025 8030 8040 8046; do
        iptables -A nixos-fw -i enp3s0 -s "$net" -p tcp --dport "$port" -j nixos-fw-accept
      done
    done
  '';
}
