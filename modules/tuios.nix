{ tuiosPkg, ... }:

let
  # Memory cap for everything in the tuios cgroup (all panes: agents, tests,
  # dev servers). On 2026-11-03 (17:21 and 17:28) a `node --test` run inside a
  # pane, inspecting a cyclic fake DOM on an assertion failure, grew past 20 GB
  # and drove the 31 GB VM into global OOM; systemd then stopped the whole unit
  # (OOMPolicy=stop) and every pane died. With the cap the kernel OOM-kills the
  # runaway inside this cgroup first, and OOMPolicy=continue keeps the daemon
  # and the other panes alive. ~11 GB stay for the ERPNext benches (they run
  # outside tuios, ~4-5 GB RAM) and the system. MemoryHigh starts reclaim
  # well before the hard cap.
  memoryHigh = "16G"; # reclaim/throttle above this
  memoryMax = "20G"; # hard limit: cgroup-local OOM kill
in

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
      MemoryHigh = memoryHigh;
      MemoryMax = memoryMax;
      # An OOM kill of one pane process must not take down the daemon/panes.
      OOMPolicy = "continue";
    };
    environment.TERM = "xterm-256color";
  };

  # Agent integrations for Claude Code, Codex and Pi: pane state + tuios skill.
  # The settings files are mutable, so `tuios integration install` merges its
  # version-marked entries in at every activation (no-op when current). Same
  # block as nixos_llm_srv modules/services/tuios-server.nix.
  # The read-only MCP lets agents inspect/wait on panes, report state and send
  # mail, but cannot type into another pane. Codex asks once per machine to
  # trust new hooks.
  system.userActivationScripts.tuiosIntegrations = ''
    if [ "$(id -un)" = cristian ]; then
      ${tuiosPkg}/bin/tuios integration install claude-code codex pi --mcp \
        || echo "tuios integration install failed (non-fatal)" >&2
    fi
  '';
}
