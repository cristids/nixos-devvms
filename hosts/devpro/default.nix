{ ... }:

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

  # No Codex Remote Control on devpro (2026-10-01): it needs OpenAI's standalone
  # install under ~/.codex/packages, which shadowed the Nix codex on PATH. The
  # Nix codex (pinned in ../../modules/agents.nix) is the only one here.
  systemd.user.services.codex-app-server.enable = false;

  # ERPNext dev benches (`bench serve`): 8001 (uat), 8030 (test3), 8040 (test4) and mailpit 8025,
  # reachable only from Work (.15, laptops) and Servers (.40) — not the whole LAN.
  # 8001/8025 used to be hand-inserted iptables rules that a firewall reload drops.
  networking.firewall.extraCommands = ''
    for net in 192.168.15.0/24 192.168.40.0/24; do
      for port in 8001 8025 8030 8040; do
        iptables -A nixos-fw -i enp3s0 -s "$net" -p tcp --dport "$port" -j nixos-fw-accept
      done
    done
  '';
}
