{ pkgs, ... }:

# T3 Code server (`t3 serve`) — chat view for Claude Code, Codex and pi/pi-admin
# on this VM, next to herdr-core. Same unit as cdssrv02 (nixos_llm_srv
# modules/services/t3code.nix); package copy in ../pkgs/t3code (bump together).
#
# Access: http://<vm LAN IP>:3773 (devpro .26 / devhobby .27). 3773 is open on
# enp3s0; the pairing code from `t3 pair` (run as cristian here) is the only
# gate. Updates via Nix only — no `t3 update` / `t3 service install`.

let
  user = "cristian";
  port = 3773;
  t3code = pkgs.callPackage ../pkgs/t3code { };
in
{
  environment.systemPackages = [ t3code ];
  networking.firewall.interfaces."enp3s0".allowedTCPPorts = [ port ];

  users.users.${user}.linger = true;

  systemd.user.services.t3code = {
    description = "T3 Code server — chat control plane for coding agents";
    documentation = [ "https://github.com/pingdotgg/t3code" ];
    wantedBy = [ "default.target" ];
    unitConfig.ConditionUser = user;
    # Restarting interrupts running agent turns: a switch must not do it.
    restartIfChanged = false;
    stopIfChanged = false;
    serviceConfig = {
      Type = "simple";
      WorkingDirectory = "%h";
      ExecStart = "${t3code}/bin/t3 serve --mode web --lan --port ${toString port} --no-browser";
      TimeoutStopSec = 30;
      Restart = "on-failure";
      RestartSec = 5;
      KillMode = "mixed";
      MemoryHigh = "16G";
      MemoryMax = "20G";
      OOMPolicy = "continue";
      # /run/wrappers/bin first (setuid sudo). Agents come from the system profile.
      Environment = [
        "PATH=/run/wrappers/bin:/run/current-system/sw/bin"
        "T3CODE_TELEMETRY_ENABLED=false"
      ];
    };
  };
}
