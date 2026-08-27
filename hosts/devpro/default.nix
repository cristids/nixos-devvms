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

  microvm.interfaces = [{
    type = "macvtap";
    id = "vm-devpro";
    # Locally-administered MAC, mnemonic "cds s03 dev 01" (historical — the VMs
    # started life on cdssrv03; kept unchanged across the 2026-08-27 move so the
    # DHCP reservation to 192.168.40.26 survives).
    mac = "02:cd:53:03:de:01";
    macvtap = {
      link = "eno1np0"; # cdssrv02's uplink NIC
      mode = "bridge";
    };
  }];
}
