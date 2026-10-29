{ tuiosPkg, ... }:

# tuios — terminal multiplexer (the `tuios` flake input, same tag as
# nixos_llm_srv and nixos-laptops: the link protocol is versioned). Its daemon runs as a lingering per-user service so
# sessions survive the ssh login that started them. Since 0.8.5 other machines
# can open panes here over ssh; the default [hosts] policy (list, mail, open,
# write — not respond) is fine, so no config is shipped.
{
  environment.systemPackages = [ tuiosPkg ];

  users.users.cristian.linger = true;

  systemd.user.services.tuios = {
    description = "tuios daemon (persistent terminal sessions)";
    wantedBy = [ "default.target" ];
    # A NixOS switch must never interrupt panes that contain working agents or
    # subagents. On 2026-10-29 the automatic devpro switch stopped this unit
    # and systemd SIGKILLed three Codex processes in its cgroup. The new unit
    # file is loaded now and takes effect on the next deliberate restart/boot.
    restartIfChanged = false;
    stopIfChanged = false;
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

  # Agent integrations for Claude Code and Codex: pane state + tuios skill.
  # The settings files are mutable, so `tuios integration install` merges its
  # version-marked entries in at every activation (no-op when current). Same
  # block as nixos_llm_srv modules/services/tuios-server.nix.
  # Codex asks once per machine to trust new hooks.
  system.userActivationScripts.tuiosIntegrations = ''
    if [ "$(id -un)" = cristian ]; then
      ${tuiosPkg}/bin/tuios integration install claude-code codex \
        || echo "tuios integration install failed (non-fatal)" >&2
    fi
  '';
}
