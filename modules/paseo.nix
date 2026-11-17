{ lib, pkgs, paseoPkg, ... }:

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

  # paseo-hc <command> [args...]: used by the "(herdr-core)" terminal profiles in
  # ~/.paseo/config.json (2026-11-16). Starts the command as a herdr-core
  # terminal (herdr-core.nix, `hc`; the one the Herdr phone app sees) and
  # attaches this Paseo terminal to it. The program is a child of the
  # herdr-core daemon with no parent terminal, not of Paseo, so it survives a
  # Paseo restart/switch (Paseo's reaper only kills descendants of its own
  # terminal: here just the `hc attach` client) and stays usable without
  # Paseo: `hc`, `hc attach <name>`, or the phone app.
  paseo-hc = pkgs.writeShellApplication {
    name = "paseo-hc";
    # hc itself comes from the system profile (herdr-core.nix).
    runtimeInputs = [ pkgs.jq pkgs.coreutils ];
    text = ''
      if [ "$#" -eq 0 ]; then
        echo "usage: paseo-hc <command> [args...]" >&2; exit 2
      fi
      # Paseo passes {{{prompt}}} as an empty arg when there is no prompt.
      args=()
      for a in "$@"; do [ -n "$a" ] && args+=("$a"); done
      prog=$(basename "''${args[0]}" | tr -c 'a-z0-9_\n-' '-')
      name="''${prog:0:24}-$(date +%H%M%S)"
      out=$(hc run --bg --json --no-parent --name "$name" --cwd "$PWD" \
        --label source=paseo -- "''${args[@]}")
      addr=$(jq -r '.address' <<<"$out")
      exec hc attach "$addr"
    '';
  };
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

  # On the system PATH (not paseo's unit PATH) so adding it does not restart Paseo.
  environment.systemPackages = [ paseo-hc ];

  networking.firewall.interfaces."enp3s0".allowedTCPPorts = [ port ];
}
