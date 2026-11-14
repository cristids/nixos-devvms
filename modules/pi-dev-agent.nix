{ config, lib, pkgs, piAdminLean, ... }:

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
    resolveBefore = "2026-11-14";
    bins = { mcp-searxng = "mcp-searxng/dist/cli.js"; };
    meta.description = "SearXNG MCP server for trusted pi-task workers";
  };
  # lean-coding skill: one file, from the cristids/pi-admin flake input (also used by pi-admin).
  leanSkills = pkgs.linkFarm "pi-task-lean-coding-skill" [ { name = "lean-coding"; path = piAdminLean.skillDir; } ];
  leanRulesFile = if cfg.leanCodingDefault then "${piAdminLean.skillFile}" else "";
  costReport = pkgs.runCommand "pi-task-cost-report" { } ''
    install -Dm755 ${./pi-task-cost-report.py} $out/bin/pi-task-cost-report
    patchShebangs $out/bin
  '';
  productionBenches = [ "test" "test1" "test2" "test3" "test4" "test5" "test6" "test7" ];
  browserPython = pkgs.python3.withPackages (p: [ p.playwright p.requests ]);
  piBrowserPython = pkgs.writeShellScriptBin "pi-browser-python" ''
    export PYTHONNOUSERSITE=1
    exec ${browserPython}/bin/python3 "$@"
  '';

  piTask = pkgs.runCommand "pi-task" {
    nativeBuildInputs = [ pkgs.makeWrapper pkgs.python3 pkgs.git pkgs.strace config.ompDevAgent.bubblewrap ];
  } ''
    cp ${./pi-task.py} pi-task.py
    cp ${./pi-tested-models.json} pi-tested-models.json
    cp ${./test_pi_task.py} test_pi_task.py
    substituteInPlace pi-task.py --replace-fail '"@LEAN_RULES@"' '"${leanRulesFile}"'
    PI_TASK_EXPECT_LEAN=${if cfg.leanCodingDefault then "1" else "0"} ${pkgs.python3}/bin/python3 -m unittest -v test_pi_task.py
    install -Dm755 pi-task.py $out/libexec/pi-task.py
    install -Dm644 pi-tested-models.json $out/libexec/pi-tested-models.json
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
    cp ${./pi-tested-models.json} pi-tested-models.json
    cp ${./test_pi_task_control.py} test_control.py
    ${pkgs.python3}/bin/python3 -m unittest -v test_control.py
    substituteInPlace pi-task-control.py \
      --replace-fail '#!/usr/bin/env python3' '#!${pkgs.python3}/bin/python3'
    install -Dm755 pi-task-control.py $out/bin/pi-task-control
    install -Dm644 pi-tested-models.json $out/bin/pi-tested-models.json
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
          input = [ "text" "image" ];
          reasoning = true;
          contextWindow = 262144;
          # Provider maximum unavailable: explicitly configured fallback cap.
          maxTokens = 65536;
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
        name = "Melious tested coding models (localhost proxy)";
        # The wrapper replaces this fail-closed URL with /w/WORKER_ID/v1.
        baseUrl = "http://127.0.0.1:5098/w/unregistered/v1";
        api = "openai-completions";
        apiKey = "credential-held-by-local-proxy";
        inherit compat;
        models = map (model: {
          id = model.id;
          # Melious input_modalities catalog, verified 2026-11-07. Pi supports
          # image attachments here, not the catalog's additional video modality.
          input = [ "text" ] ++ lib.optional (builtins.elem model.id [
            "deepseek-v4.1-flash-1017" "glm-5.3-flash" "kimi-k3" "kimi-k2.7-code"
          ]) "image";
          reasoning = true;
          contextWindow = model.contextWindow;
          # Melious catalog max_output_tokens is null (2026-11-07).
          maxTokens = 65536;
          # Verified bake-off mapping, credits per 1M tokens. The persistent
          # control service reserves and settles every worker-scoped request.
          cost = { inherit (model) input output cacheRead; cacheWrite = 0; };
          samplingParams = {
            temperature = 1.0;
            top_p = if builtins.elem model.id [ "glm-5.3" "glm-5.3-flash" "kimi-k3" ] then 1.0 else 0.95;
          };
        }) (builtins.attrValues (builtins.fromJSON (builtins.readFile ./pi-tested-models.json)));
      };
    };
  });

  settings = pkgs.writeText "pi-local-qwen-settings.json" (builtins.toJSON {
    enableInstallTelemetry = false;
    enableAnalytics = false;
    # available to every worker; also on by default in the worker rules when leanCodingDefault
    skills = [ "${leanSkills}" ];
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
    leanCodingDefault = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Append the lean-coding skill (cristids/pi-admin) to every pi-task worker's rules.";
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ piPkg piTask piTaskControl webMcpPkg costReport ]
      ++ lib.optionals cfg.erpnext.enable [ pkgs.chromium pkgs.playwright-test piBrowserPython ];
    environment.etc."pi-task-browser-env" = lib.mkIf cfg.erpnext.enable {
      text = ''
        export E2E_CHROMIUM=${pkgs.chromium}/bin/chromium
        export NODE_PATH=${pkgs.playwright-test}/lib/node_modules
        export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
      '';
    };

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
