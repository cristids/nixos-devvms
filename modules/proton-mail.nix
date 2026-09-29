{ pkgs, ... }:

# READ-ONLY Proton mail for devpro (2026-09-29), via himalaya.
#
# IMAP goes to the host's read-only proxy on vmshim0 (192.168.40.28:1144), which
# rejects every mutating IMAP verb (STORE/EXPUNGE/APPEND/DELETE/COPY/MOVE/…) and
# rewrites SELECT→EXAMINE — agents here can read mail but cannot change it, even
# under prompt injection. The host firewall admits only devpro (.26) on that port;
# the raw read-write Bridge (:1143) is not reachable from this VM.
#
# DELIBERATELY no SMTP section: this VM never sends mail (the host drops the
# Bridge SMTP port as well). ERPNext keeps its mailpit sink (192.168.40.15:1026).
#
# Plain IMAP on purpose: guest↔vmshim0 traffic stays inside the host kernel
# (macvtap/macvlan bridge mode), and the stdlib proxy does not pass STARTTLS.
#
# The Bridge-generated IMAP password (NOT the real Proton password) is not in the
# nix store or this repo; install it once from a machine that has it:
#
#   ssh devpro 'sudo install -m 600 -o cristian -g users /dev/stdin /var/lib/proton-imap.pw' <<< "<bridge password>"
let
  himalayaConfig = pkgs.writeText "himalaya-config.toml" ''
    [accounts.proton]
    email = "cristids@proton.me"
    display-name = "Cristi (Proton, read-only)"
    default = true

    backend.type = "imap"
    backend.host = "192.168.40.28"
    backend.port = 1144
    backend.encryption.type = "none"
    backend.login = "cristids@proton.me"
    backend.auth.type = "password"
    backend.auth.cmd = "cat /var/lib/proton-imap.pw"
  '';
in
{
  environment.systemPackages = [ pkgs.himalaya ];
  environment.variables.HIMALAYA_CONFIG = "${himalayaConfig}";

  # Also link it at himalaya's default path: sessions started before a deploy
  # (long-lived herdr/tmux panes, agents) never see the new environment variable.
  systemd.tmpfiles.rules = [
    "d /home/cristian/.config/himalaya 0755 cristian users -"
    "L+ /home/cristian/.config/himalaya/config.toml - - - - ${himalayaConfig}"
  ];
}
