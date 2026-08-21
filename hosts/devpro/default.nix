{ ... }:

# devpro — professional work. Will eventually hold SCOPED work credentials (e.g. an
# Azure DevOps key), so agents in it get driven with more care than in devhobby.
# Everything else: ../common.
{
  networking.hostName = "devpro";

  microvm.interfaces = [{
    type = "macvtap";
    id = "vm-devpro";
    # Locally-administered MAC, mnemonic "cds s03 dev 01". Router DHCP reservation
    # pins it to 192.168.40.108.
    mac = "02:cd:53:03:de:01";
    macvtap = {
      link = "enp0s31f6";
      mode = "bridge";
    };
  }];
}
