{ tuiosPkg, ... }:

# tuios — terminal multiplexer (from nixpkgs-unstable; stable 25.11 lacks a
# recent enough build). Its daemon runs as a lingering per-user service so
# sessions survive the ssh login that started them. Since 0.8.5 other machines
# can open panes here over ssh; the default [hosts] policy (list, mail, open,
# write — not respond) is fine, so no config is shipped.
{
  environment.systemPackages = [ tuiosPkg ];

  users.users.cristian.linger = true;

  systemd.user.services.tuios = {
    description = "tuios daemon (persistent terminal sessions)";
    wantedBy = [ "default.target" ];
    unitConfig.ConditionUser = "cristian";
    serviceConfig = {
      Type = "simple";
      ExecStart = "${tuiosPkg}/bin/tuios daemon";
      ExecStop = "${tuiosPkg}/bin/tuios kill-server";
      TimeoutStopSec = 30;
      Restart = "on-failure";
      RestartSec = 5;
      KillMode = "mixed";
    };
    environment.TERM = "xterm-256color";
  };
}
