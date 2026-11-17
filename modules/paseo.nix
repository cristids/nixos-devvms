{ lib, paseoPkg, ... }:

# Paseo daemon (getpaseo/paseo v0.16.1) on devpro — same unit as cdssrv02
# (nixos_llm_srv modules/services/paseo.nix); bump the flake input and the
# npmDepsHash below together with that repo.
#
# Access: http://192.168.40.26:6767 on the LAN (web UI + apps). 6767 open on
# enp3s0 only. Relay OFF, no daemon password (trusted LAN, as on cdssrv02).
# `settings` stays empty ON PURPOSE: the module rewrites config.json from it on
# every start, which would wipe providers/terminal profiles configured at
# runtime in ~/.paseo/config.json.
#
# Runs as cristian (state in ~/.paseo) so agents use his claude/codex/pi logins.

let
  user = "cristian";
  port = 6767;
in
{
  services.paseo = {
    enable = true;
    # Same override as cdssrv02 (upstream getpaseo/paseo#7673 + npmDepsHash of
    # our pinned paseo rev): identical derivation, so it substitutes from there.
    package = (paseoPkg.override {
      npmDepsHash = "sha256-k7+5S/4tyAmHdZmUdhpuZ7GOc7hp9cG8wUBFdJI32ng=";
    }).overrideAttrs (old: {
      preBuild = (old.preBuild or "") + "\nnpm run postinstall\n";
    });
    inherit user port;
    group = "users";
    listenAddress = "0.0.0.0";
    relay.enable = false;
    inheritUserEnvironment = false;
    environment.PASEO_WEB_UI_ENABLED = "true";
  };

  # setuid wrappers FIRST (sudo); pi, rg, fd, hc come from the system profile.
  systemd.services.paseo.environment.PATH = lib.mkForce
    "/run/wrappers/bin:/etc/profiles/per-user/${user}/bin:/run/current-system/sw/bin";

  systemd.services.paseo.serviceConfig = {
    KillMode = "mixed";
    MemoryHigh = "16G";
    MemoryMax = "20G";
    OOMPolicy = "continue";
  };

  networking.firewall.interfaces."enp3s0".allowedTCPPorts = [ port ];
}
