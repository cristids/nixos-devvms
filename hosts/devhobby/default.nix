{ ... }:

# devhobby — hobby projects / agent playground. Carries only its own Melious key and
# hobby-scoped tokens; the freedom to let agents run loose here is the point of
# keeping it separate from devpro. Everything else: ../common.
{
  networking.hostName = "devhobby";

  microvm.interfaces = [{
    type = "macvtap";
    id = "vm-devhobby";
    # Locally-administered MAC, mnemonic "cds s03 dev 02". Router DHCP reservation
    # pins it to 192.168.40.109.
    mac = "02:cd:53:03:de:02";
    macvtap = {
      link = "enp0s31f6";
      mode = "bridge";
    };
  }];
}
