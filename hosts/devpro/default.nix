{ ... }:

# devpro — professional work. Will eventually hold SCOPED work credentials (e.g. an
# Azure DevOps key), so agents in it get driven with more care than in devhobby.
# Everything else: ../common.
{
  imports = [
    # UAT ERPNext (rootless podman, opt-in via `erpnext-uat up`). devpro only —
    # devhobby has no business running the company's ERP.
    ../../modules/erpnext-uat.nix
  ];

  networking.hostName = "devpro";
}
