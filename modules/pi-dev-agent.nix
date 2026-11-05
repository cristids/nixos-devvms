{ config, lib, pkgs, ... }:

# Production Pi worker backed by the local Qwen 3.8 Flash endpoint. This is
# deliberately separate from devhobby's recorded bake-off Pi configuration:
# devpro gets the production profile, while devhobby keeps its frozen harness.
let
  cfg = config.piDevAgent;
  home = "/home/cristian";
  sw = "/run/current-system/sw/bin";
  piPkg = pkgs.callPackage ../pkgs/pi-coding-agent { };

  piTask = pkgs.runCommand "pi-task" {
    nativeBuildInputs = [ pkgs.makeWrapper pkgs.python3 pkgs.git ];
  } ''
    cp ${./pi-task.py} pi-task.py
    cp ${./test_pi_task.py} test_pi_task.py
    ${pkgs.python3}/bin/python3 -m unittest -v test_pi_task.py
    install -Dm755 pi-task.py $out/libexec/pi-task.py
    makeWrapper ${pkgs.python3}/bin/python3 $out/bin/pi-task \
      --add-flags $out/libexec/pi-task.py \
      --prefix PATH : ${lib.makeBinPath [
        pkgs.bash config.ompDevAgent.bubblewrap pkgs.coreutils pkgs.git pkgs.systemd piPkg
      ]}
  '';

  models = pkgs.writeText "pi-local-qwen-models.json" (builtins.toJSON {
    providers.homelab = {
      name = "Homelab llama-swap";
      baseUrl = "http://192.168.40.28:9000/v1";
      api = "openai-completions";
      apiKey = "none";
      compat = {
        maxTokensField = "max_tokens";
        supportsDeveloperRole = false;
        supportsStore = false;
        supportsReasoningEffort = false;
        supportsUsageInStreaming = true;
      };
      models = [{
        id = "strata/qwen3.8-flash";
        reasoning = true;
        contextWindow = 262144;
        maxTokens = 32768;
        cost = { input = 0; output = 0; cacheRead = 0; cacheWrite = 0; };
        samplingParams = {
          temperature = 1.0;
          top_p = 0.95;
          top_k = 20;
          min_p = 0.0;
          presence_penalty = 0.0;
        };
      }];
    };
  });

  settings = pkgs.writeText "pi-local-qwen-settings.json" (builtins.toJSON {
    enableInstallTelemetry = false;
    enableAnalytics = false;
    defaultProvider = "homelab";
    defaultModel = "strata/qwen3.8-flash";
  });

  mcp = pkgs.writeText "pi-production-mcp.json" (builtins.toJSON {
    mcpServers = {
      context7 = {
        command = "${sw}/context7-mcp";
        args = [ ];
        exposure = "direct";
      };
      tuios = {
        command = "${sw}/tuios";
        args = [ "mcp" ];
        exposure = "direct";
      };
    } // lib.optionalAttrs cfg.erpnext.enable {
      erpnext = {
        command = "${sw}/erpnext-mcp";
        args = [ ];
        exposure = "direct";
      };
    };
  });
in
{
  options.piDevAgent = {
    enable = lib.mkEnableOption "Pi backed by local Qwen 3.8 Flash";
    erpnext.enable = lib.mkEnableOption "the ERPNext MCP in Pi";
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ piPkg piTask ];

    # These files contain no credentials. Keep auth.json and conversation state
    # untouched; only the declarative provider/settings/MCP files are replaced.
    systemd.user.services.pi-dev-config = {
      description = "Configure Pi for local Qwen and production MCPs";
      wantedBy = [ "default.target" ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
      script = ''
        d=${home}/.pi/agent
        install -d -m 0700 "$d"
        install -m 0644 ${models} "$d/models.json"
        install -m 0644 ${settings} "$d/settings.json"
        install -m 0644 ${mcp} "$d/mcp.json"
      '';
      restartTriggers = [ models settings mcp ];
    };

    # The system activation hook can run before pi-dev-config has created
    # ~/.pi/agent on a fresh host. Install the native TUIOS Pi plugin only
    # after that directory exists; the command is idempotent and versioned.
    systemd.user.services.pi-tuios-integration = {
      description = "Install the TUIOS plugin for Pi";
      wantedBy = [ "default.target" ];
      after = [ "pi-dev-config.service" ];
      requires = [ "pi-dev-config.service" ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
      script = ''
        ${sw}/tuios integration install pi
      '';
    };
  };
}
