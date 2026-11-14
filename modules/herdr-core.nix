# herdr-core (`hc`) — terminal runtime for coding agents, headless.
#
#   hc run --bg --name review pi "review src/x.rs. Done: hc send $HC_TERMINAL_ID"
#   hc wait review && hc read --lines 60 review
#   hc machine add cdssrv02 && hc ls      # local + remote terminals in one list
#
# The Herdr phone app (iOS/iPad/Android) speaks to herdr-core over SSH: no port,
# no account, nothing exposed on this VM. herdr TUI is not involved.
#
# Same module as nixos_llm_srv/modules/services/herdr-core.nix, so the two
# servers behave identically. Statically linked prebuilt binary from Herdr's own
# release server; version + hash from
# https://releases.herdr.dev/core/latest.json — bump both together.
#
# Runs as cristian (user unit + linger): the agents it starts need that user's
# keys, git config and agent CLIs.
{ pkgs, lib, ... }:

let
  version = "0.1.21";
  arch = if pkgs.stdenv.hostPlatform.isx86_64 then "x86_64" else "aarch64";

  herdr-core = pkgs.runCommand "herdr-core-${version}" {
    nativeBuildInputs = [ pkgs.gnutar pkgs.gzip ];
    dontPatchELF = true;
    dontStrip = true;
    meta = {
      description = "herdr-core: terminals for coding agents (hc)";
      homepage = "https://herdr.dev/core/";
      license = lib.licenses.asl20;
      mainProgram = "hc";
    };
  } ''
    mkdir -p $out/bin
    tar -xzf ${
      pkgs.fetchurl {
        url = "https://releases.herdr.dev/core/${version}/herdr-core-linux-${arch}.tar.gz";
        hash = "sha256-eEJHVcEKQN3jigvbVcIKwZcG6a4F7Wj8Ks8fz/DZQcE=";
      }
    } -C . herdr-core
    install -m 0555 herdr-core $out/bin/herdr-core
    ln -s herdr-core $out/bin/hc
  '';
in
{
  environment.systemPackages = [ herdr-core ];

  users.users.cristian.linger = true;

  systemd.user.services.herdr-core = {
    description = "herdr-core — terminals for coding agents (hc)";
    documentation = [ "https://herdr.dev/core/" ];
    wantedBy = [ "default.target" ];
    unitConfig.ConditionUser = "cristian";

    # Same rule as tuios: a switch must never kill terminals holding agents.
    restartIfChanged = false;
    stopIfChanged = false;

    serviceConfig = {
      Type = "simple";
      ExecStart = "${herdr-core}/bin/herdr-core serve";
      TimeoutStopSec = 30;
      Restart = "on-failure";
      RestartSec = 5;
      KillMode = "mixed";
      # This VM has ~31 GB: keep a runaway child from taking the whole guest
      # down, and let the daemon survive the cgroup OOM kill.
      MemoryHigh = "16G";
      MemoryMax = "20G";
      OOMPolicy = "continue";
    };

    environment = {
      TERM = "xterm-256color";
    };
    # The daemon's environment is what spawned terminals inherit (the hc CLI
    # does not pass the caller's PATH), so every agent has to be here.
    serviceConfig.Environment = [
      "PATH=/run/current-system/sw/bin:/run/wrappers/bin"
    ];
  };
}
