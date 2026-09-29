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
}
