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
}
