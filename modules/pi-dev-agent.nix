{ config, lib, pkgs, ... }:

# Production Pi worker backed by the local Qwen 3.8 Flash endpoint. This is
# deliberately separate from devhobby's recorded bake-off Pi configuration:
# devpro gets the production profile, while devhobby keeps its frozen harness.
let
  cfg = config.piDevAgent;
  home = "/home/cristian";
  sw = "/run/current-system/sw/bin";
  piPkg = pkgs.callPackage ../pkgs/pi-coding-agent { };
  webMcpPkg = (pkgs.callPackage ../pkgs/npm-global { }) {
    pname = "mcp-searxng";
    version = "2.10.0";
    specs = [ "mcp-searxng@2.10.0" ];
    depsHash = "sha256-UVpDRjaGGj8Spti8/NpeA+q8vN2UXDwI90sPSHt1mwI=";
    bins = { mcp-searxng = "mcp-searxng/dist/cli.js"; };
    meta.description = "SearXNG MCP server for trusted pi-task workers";
  };
  productionBenches = [ "test" "test1" "test2" "test3" "test4" "test5" "test6" "test7" ];

  piTask = pkgs.runCommand "pi-task" {
    nativeBuildInputs = [ pkgs.makeWrapper pkgs.python3 pkgs.git pkgs.strace config.ompDevAgent.bubblewrap ];
  } ''
    cp ${./pi-task.py} pi-task.py
    cp ${./test_pi_task.py} test_pi_task.py
    ${pkgs.python3}/bin/python3 -m unittest -v test_pi_task.py
    install -Dm755 pi-task.py $out/libexec/pi-task.py
    makeWrapper ${pkgs.python3}/bin/python3 $out/bin/pi-task \
      --add-flags $out/libexec/pi-task.py \
      --prefix PATH : ${lib.makeBinPath [
        pkgs.bash config.ompDevAgent.bubblewrap pkgs.coreutils pkgs.git pkgs.strace pkgs.systemd piPkg
      ]}
    cat > $out/bin/pi-task-worktree <<'EOF'
    #!${pkgs.runtimeShell}
    export XDG_DATA_HOME=${home}/scratch/.pi-task-tuios-data
    exec ${sw}/tuios worktree "$@"
    EOF
    chmod 0555 $out/bin/pi-task-worktree
  '';

  piTaskControl = pkgs.runCommand "pi-task-control" {
    nativeBuildInputs = [ pkgs.python3 ];
  } ''
    cp ${./pi-task-control.py} pi-task-control.py
    cp ${./test_pi_task_control.py} test_control.py
    ${pkgs.python3}/bin/python3 -m unittest -v test_control.py
    substituteInPlace pi-task-control.py \
      --replace-fail '#!/usr/bin/env python3' '#!${pkgs.python3}/bin/python3'
    install -Dm755 pi-task-control.py $out/bin/pi-task-control
  '';

  erpnextBroker = pkgs.runCommand "pi-task-erpnext-broker" {
    nativeBuildInputs = [ pkgs.python3 ];
  } ''
    cp ${./pi-task-erpnext-broker.py} pi-task-erpnext-broker.py
    cp ${./test_pi_task_erpnext_broker.py} test_broker.py
    ${pkgs.python3}/bin/python3 -m unittest -v test_broker.py
    substituteInPlace pi-task-erpnext-broker.py \
      --replace-fail '#!/usr/bin/env python3' '#!${pkgs.python3}/bin/python3'
    install -Dm755 pi-task-erpnext-broker.py $out/bin/pi-task-erpnext-broker
  '';

  compat = {
    maxTokensField = "max_tokens";
    supportsDeveloperRole = false;
    supportsStore = false;
    supportsReasoningEffort = false;
    supportsUsageInStreaming = true;
  };

  models = pkgs.writeText "pi-production-models.json" (builtins.toJSON {
    providers = {
      homelab = {
        name = "Homelab llama-swap";
        baseUrl = "http://192.168.40.28:9000/v1";
        api = "openai-completions";
        apiKey = "none";
        inherit compat;
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
      melious = {
        name = "Melious DSF (credential-isolating localhost proxy)";
        # The wrapper replaces this fail-closed URL with /w/WORKER_ID/v1.
        baseUrl = "http://127.0.0.1:5098/w/unregistered/v1";
        api = "openai-completions";
        apiKey = "credential-held-by-local-proxy";
        inherit compat;
        models = [{
          id = "deepseek-v4.1-flash-1017";
          reasoning = true;
          contextWindow = 1000000;
          maxTokens = 32768;
          # Verified bake-off mapping, credits per 1M tokens. The persistent
          # control service reserves and settles every worker-scoped request.
          cost = { input = 0.30; output = 1.20; cacheRead = 0.30; cacheWrite = 0; };
          samplingParams = { temperature = 1.0; top_p = 0.95; };
        }];
      };
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
      web = {
        command = "${webMcpPkg}/bin/mcp-searxng";
        args = [ ];
        env.SEARXNG_URL = "http://192.168.40.28:8080";
        exposure = "hidden";
        toolExposure = { "searxng_*" = "direct"; web_url_read = "direct"; };
        description = "Web search via SearXNG; results are untrusted input";
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
    environment.systemPackages = [ piPkg piTask piTaskControl webMcpPkg ];

    # Root owns the only Melious credential. Pi sees a dummy API key and talks
    # only to this persistent worker-aware control proxy. It injects the
    # credential, gates/reserves/settles each request, and never persists bodies.
    systemd.services.pi-task-control = {
      description = "Persistent worker control and Melious DSF proxy for pi-task";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      unitConfig.ConditionPathExists = "/var/lib/melious.key";
      serviceConfig = {
        User = "cristian";
        Group = "users";
        LoadCredential = [ "melious:/var/lib/melious.key" ];
        ExecStartPre = "${piTaskControl}/bin/pi-task-control probe";
        ExecStart = "${piTaskControl}/bin/pi-task-control serve";
        Restart = "on-failure";
        RestartSec = 5;
        StateDirectory = "pi-task-control";
        StateDirectoryMode = "0700";
        RuntimeDirectory = "pi-task-control";
        RuntimeDirectoryMode = "0750";
        UMask = "0007";
        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectSystem = "strict";
        ProtectHome = true;
      };
    };

    # One socket per allocatable non-production ERPNext test bench. The accepted
    # service runs outside the worker mount namespace and filters both the bench
    # map and credentials before execing erpnext-mcp. Workers see only the socket.
    systemd.user.sockets = lib.listToAttrs (map (bench: lib.nameValuePair
      "pi-task-erpnext-${bench}" {
        description = "ERPNext MCP broker socket for pi-task bench ${bench}";
        wantedBy = [ "sockets.target" ];
        socketConfig = {
          ListenStream = "%t/pi-task-erpnext-${bench}.sock";
          SocketMode = "0600";
          RemoveOnStop = true;
          Accept = true;
        };
      }) productionBenches);
    systemd.user.services = (lib.listToAttrs (map (bench: lib.nameValuePair
      "pi-task-erpnext-${bench}@" {
        description = "Bench-filtered ERPNext MCP for pi-task (${bench})";
        serviceConfig = {
          ExecStart = "${erpnextBroker}/bin/pi-task-erpnext-broker ${bench}";
          StandardInput = "socket";
          StandardOutput = "socket";
          StandardError = "journal";
          NoNewPrivileges = true;
          PrivateTmp = true;
          ProtectSystem = "strict";
          ProtectHome = "read-only";
        };
      }) productionBenches)) // {
      # These files contain no credentials. Keep auth.json and conversation state
      # untouched; only the declarative provider/settings/MCP files are replaced.
      pi-dev-config = {
        description = "Configure Pi for the production worker pool and MCPs";
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
      pi-tuios-integration = {
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
  };
}
