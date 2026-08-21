{ pkgs, ... }:

# devhobby — hobby projects / agent playground. Carries only its own Melious key and
# hobby-scoped tokens; the freedom to let agents run loose here is the point of
# keeping it separate from devpro. Everything else: ../common.
{
  networking.hostName = "devhobby";

  # Hobby languages (not in devpro on purpose): Common Lisp + Prolog. Emacs-side
  # tooling (sly/slime, ediprolog…) lives in the personal emacs config, not here.
  environment.systemPackages = with pkgs; [
    sbcl
    swi-prolog
  ];

  microvm.interfaces = [{
    type = "macvtap";
    id = "vm-devhobby";
    # Locally-administered MAC, mnemonic "cds s03 dev 02". Router DHCP reservation
    # pins it to 192.168.40.27.
    mac = "02:cd:53:03:de:02";
    macvtap = {
      link = "enp0s31f6";
      mode = "bridge";
    };
  }];
}
