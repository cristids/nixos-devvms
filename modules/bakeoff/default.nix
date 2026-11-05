{ config, lib, pkgs, ... }:

# Open-weight team bake-off (2026-11-05, devhobby only). Plan:
# ~/.claude/plans/ok-here-is-waht-precious-goose.md on cdssrv02. Experiment DATA lives in
# ~cristian/bakeoff (runs, benches, snapshots, ledger) and is archived to cdssrv02 /tank,
# never in this repo. Pieces:
#
#   pi + taskplane     pi 1.5.0 with taskplane 0.30.6 as a local pi package (../../pkgs/pi-coding-agent)
#   dsh                DeepSeek Harness CLI, telemetry hard-disabled (../../pkgs/dsh)
#   bakeoff-proxy      127.0.0.1:5099, the ONLY path to models: injects the Melious key (LoadCredential,
#                      agents never see it), records every call (raw JSONL + summary), classifies
#                      outcomes, clamps max output tokens to 32768, retries 429s, serializes the
#                      single-stream local Qwen Flash, enforces the 200-credit total Melious cap
#                      (no new runs past it) and per-run spend/turn caps. ./proxy.py
#   pi-models-seed     user oneshot: ~/.pi/agent/{models,settings,mcp}.json for interactive pi via
#                      the proxy (run id "interactive"); providers melious + homelab.
#   bakeoff-pi/-dsh    one sandboxed, recorded run (omp-task bwrap posture, 6G scope). ./bakeoff-pi.sh
#   bakeoff-bench      throwaway Frappe benches b1..b3 (fresh sites, snapshot reset). ./bakeoff-bench.sh
#   MariaDB 11.4       local, for the benches (root via unix socket only).
#
# Secrets (root 0600, never printed, never in the store): /var/lib/melious.key (devpro's key,
# copied root->root) and /var/lib/bakeoff/web-mcp.token (cdssrv02 web MCP gateway :9542 token).
let
  home = "/home/cristian";
  piPkg = pkgs.callPackage ../../pkgs/pi-coding-agent { };
  dshPkg = pkgs.callPackage ../../pkgs/dsh { };
  frappeBench = pkgs.callPackage ../../pkgs/frappe-bench { };
  mariadb = pkgs.mariadb_114;

  py = pkgs.python3.withPackages (ps: [ ps.aiohttp ]);
  # The offline classification tests run as part of the build (a red test = no deploy).
  proxyTests = pkgs.runCommand "bakeoff-proxy-tests" {
    nativeBuildInputs = [ (pkgs.python3.withPackages (ps: [ ps.aiohttp ps.pytest ])) ];
  } ''
    cp ${./proxy.py} proxy.py; cp ${./test_proxy.py} test_proxy.py
    HOME=$TMPDIR python3 -m pytest -q -p no:cacheprovider test_proxy.py
    touch $out
  '';
  proxyPy = pkgs.runCommand "bakeoff-proxy.py" { } ''
    # tests: ${proxyTests}
    cp ${./proxy.py} $out
  '';

  # ── Model roster (Phase 0 probes, 2026-11-05; ids from the live Melious catalog) ──
  # cost = USD per 1M tokens derived from billing_cost (1 credit = 1 USD), logging only.
  # Sampling = official model cards (HF, fetched 11-05); none where the card gives none.
  # maxTokens 32768 for every model (user decision; the proxy clamps as well).
  # Thinking: provider default (reasoning_effort not sent → GLM/Kimi/DeepSeek default max);
  # reasoning_content is replayed by pi automatically.
  melious = [
    { id = "glm-5.3"; ctx = 1000000; cost = [ 1.00 3.00 ]; sampling = { temperature = 1.0; top_p = 1.0; }; }
    { id = "glm-5.3-flash"; ctx = 1000000; cost = [ 0.10 0.40 ]; sampling = { temperature = 1.0; top_p = 1.0; }; }
    { id = "kimi-k3"; ctx = 1048576; cost = [ 2.80 14.00 ]; sampling = { temperature = 1.0; top_p = 1.0; }; }
    { id = "kimi-k2.7-code"; ctx = 262144; cost = [ 0.60 3.60 ]; sampling = { temperature = 1.0; top_p = 0.95; }; }
    { id = "deepseek-v4.1-flash-1017"; ctx = 1000000; cost = [ 0.30 1.20 ]; sampling = { temperature = 1.0; top_p = 0.95; }; }
    { id = "qwen3.8-27b"; ctx = 262144; cost = [ 0.40 2.40 ];
      sampling = { temperature = 1.0; top_p = 0.95; top_k = 20; min_p = 0.0; presence_penalty = 0.0; }; }
    # Qwen3.8 Max: no official sampling recommendation found → provider defaults.
    { id = "qwen3.8-max"; ctx = 256000; cost = [ 1.80 6.80 ]; sampling = { }; }
  ];
  homelab = [
    # Local Qwen 3.8 Flash-Next (strata, CONTEXT=256k, PARALLEL=1). Sampling: the Qwen3.8
    # family thinking preset (official Qwen3.8-27B card); min_p explicit (llama.cpp default 0.05).
    { id = "strata/qwen3.8-flash"; ctx = 262144; cost = [ 0 0 ];
      sampling = { temperature = 1.0; top_p = 0.95; top_k = 20; min_p = 0.0; presence_penalty = 0.0; }; }
  ];
  mkModel = m: {
    inherit (m) id;
    reasoning = true;
    contextWindow = m.ctx;
    maxTokens = 32768;
    cost = { input = builtins.elemAt m.cost 0; output = builtins.elemAt m.cost 1;
             cacheRead = builtins.elemAt m.cost 0; cacheWrite = 0; };
  } // lib.optionalAttrs (m.sampling != { }) { samplingParams = m.sampling; };
  compat = {
    maxTokensField = "max_tokens";
    supportsDeveloperRole = false;
    supportsStore = false;
    supportsReasoningEffort = false;
    supportsUsageInStreaming = true;
  };
  # @RUN@ is replaced per run by bakeoff-pi (and with "interactive" by pi-models-seed).
  modelsTemplate = pkgs.writeText "bakeoff-models.json.in" (builtins.toJSON {
    providers = {
      melious = {
        name = "Melious (via bakeoff-proxy)";
        baseUrl = "http://127.0.0.1:5099/r/@RUN@/melious/v1";
        api = "openai-completions";
        apiKey = "bakeoff-proxy-injects-the-real-key";
        inherit compat;
        models = map mkModel melious;
      };
      homelab = {
        name = "Homelab llama-swap (local Qwen Flash, via bakeoff-proxy)";
        baseUrl = "http://127.0.0.1:5099/r/@RUN@/homelab/v1";
        api = "openai-completions";
        apiKey = "none";
        inherit compat;
        models = map mkModel homelab;
      };
    };
  });
  piSettings = pkgs.writeText "bakeoff-pi-settings.json" (builtins.toJSON {
    enableInstallTelemetry = false;
    enableAnalytics = false;
    defaultProvider = "melious";
    defaultModel = "glm-5.3-flash";
  });
  # taskplane as a LOCAL pi package from the Nix store (no runtime npm install). Only
  # orchestrated runs load it (bakeoff-pi --taskplane): its ~20 orch_* tools would otherwise
  # sit in every solo/worker prompt.
  taskplanePkgDir = "${piPkg}/lib/node_modules/taskplane";

  # dsh home patch (cordis): Melious route via the proxy, default model, our MCPs, no
  # DeepSeek-official web search (no key; parity with pi: web search = the same SearXNG MCP).
  dshCompat = { maxTokensField = "max_tokens"; supportsDeveloperRole = false;
                supportsStore = false; supportsReasoningEffort = false; };
  dshModels = ms: map (m: { inherit (m) id; contextWindow = m.ctx; maxTokens = 32768; }) ms;
  # JSON is valid YAML; placeholders @RUN@ @PROVIDER@ @MODEL@ @RD@ and "@ERP@" (a JSON
  # boolean after rendering) are filled per run by bakeoff-dsh.
  dshPatch = pkgs.writeText "bakeoff-dsh.patch.yml" (builtins.toJSON [
    { id = "llm-pi-ai"; config.providers = {
        melious = { displayName = "Melious (bakeoff-proxy)"; api = "openai-completions";
          baseURL = "http://127.0.0.1:5099/r/@RUN@/melious/v1"; apiKeyEnv = "BAKEOFF_PROXY_KEY";
          compat = dshCompat; models = dshModels melious; };
        homelab = { displayName = "Homelab (bakeoff-proxy)"; api = "openai-completions";
          baseURL = "http://127.0.0.1:5099/r/@RUN@/homelab/v1"; apiKeyEnv = "BAKEOFF_PROXY_KEY";
          compat = dshCompat; models = dshModels homelab; };
      }; }
    { id = "agent-default-model"; config = { provider = "@PROVIDER@"; model = "@MODEL@"; }; }
    { id = "session-telemetry-otel"; disabled = true; }
    # No extra LLM call per session for a title (pi makes none either).
    { id = "session-title-llm"; disabled = true; }
    { id = "web-search-deepseek"; disabled = true; }
    { id = "web-deepseek-api"; disabled = true; }
    { id = "mcp-context7"; name = "@deepseek-ai/dsh-mcp-client";
      config = { serverName = "context7"; transport = "stdio";
                 command = "/run/current-system/sw/bin/context7-mcp"; args = [ ]; }; }
    { id = "mcp-web"; name = "@deepseek-ai/dsh-mcp-client";
      config = { serverName = "web"; transport = "streamable-http";
                 url = "http://127.0.0.1:5099/r/@RUN@/webmcp/mcp"; }; }
    { id = "mcp-erpnext"; name = "@deepseek-ai/dsh-mcp-client"; disabled = "@ERP@";
      config = { serverName = "erpnext"; transport = "stdio";
                 command = "/run/current-system/sw/bin/erpnext-mcp"; args = [ ];
                 env = { ERPNEXT_MCP_ENV_MAP = "@RD@/agent/erpnext-env-map.json";
                         ERPNEXT_MCP_CREDENTIALS = "@RD@/agent/erpnext-credentials.json"; }; }; }
  ]);

  runner = tool: pkgs.writeShellApplication {
    name = "bakeoff-${tool}";
    runtimeInputs = with pkgs; [ coreutils gnused jq curl python3 bubblewrap config.systemd.package ];
    bashOptions = [ ];
    text = lib.replaceStrings
      [ "@tool@" "@modelsTemplate@" "@piSettings@" "@dshPatch@" "@self@" "@taskplane@" "@orchDriver@" ]
      [ tool "${modelsTemplate}" "${piSettings}" "${dshPatch}" "${./bakeoff-pi.sh}" taskplanePkgDir "${./orch-driver.py}" ]
      (builtins.readFile ./bakeoff-pi.sh);
  };
  bubblewrap = config.ompDevAgent.bubblewrap;

  bakeoffBench = pkgs.writeShellApplication {
    name = "bakeoff-bench";
    runtimeInputs = with pkgs; [ coreutils gnused gnugrep gzip gnutar curl python3 git findutils config.systemd.package ];
    bashOptions = [ ];
    text = lib.replaceStrings [ "@benchBin@" "@mariadb@" ] [ "${frappeBench}/bin" "${mariadb}" ]
      (builtins.readFile ./bakeoff-bench.sh);
  };

  # Base-only working repo for a run (coordinator rule 11-05: agents must never reach the
  # reference): a temp branch at BASE in the full-history source, then a single-branch,
  # no-tags file:// clone (only objects reachable from BASE travel), origin removed, branch
  # `main` at BASE, no reflog of anything later.
  repoAt = pkgs.writeShellApplication {
    name = "bakeoff-repo-at";
    runtimeInputs = [ pkgs.git pkgs.coreutils ];
    text = ''
      [ $# -ge 2 ] || { echo "usage: bakeoff-repo-at <base-commit> <dest> [source-repo (default ~/bakeoff/repo)]" >&2; exit 2; }
      base=$1 dest=$2 src=''${3:-$HOME/bakeoff/repo}
      [ -e "$dest" ] && { echo "$dest exists" >&2; exit 2; }
      sha=$(git -C "$src" rev-parse --verify "$base^{commit}")
      tmp=bakeoff-base-$$
      git -C "$src" branch -f "$tmp" "$sha"
      trap 'git -C "$src" branch -D "$tmp" >/dev/null 2>&1 || true' EXIT
      git clone -q --single-branch --no-tags --branch "$tmp" "file://$src" "$dest"
      git -C "$dest" checkout -q -B main
      git -C "$dest" branch -D "$tmp" >/dev/null
      git -C "$dest" remote remove origin
      git -C "$dest" reflog expire --expire=now --all
      git -C "$dest" gc -q --prune=now
      n=$(git -C "$dest" rev-list --all | wc -l)
      [ "$(git -C "$dest" rev-parse HEAD)" = "$sha" ] || { echo "HEAD mismatch" >&2; exit 1; }
      [ "$(git -C "$dest" rev-list --all --not "$sha" | wc -l)" = 0 ] || { echo "objects past base leaked" >&2; exit 1; }
      echo "$dest at $sha ($n commits, refs: $(git -C "$dest" for-each-ref --format='%(refname:short)' | tr '
' ' '))"
    '';
  };

  # `bench` for humans and agents: the Nix frappe-bench CLI run inside the cached ERPNext
  # dev environment (python 3.14 libs, mariadb client, node) of ~/bakeoff/erpnext.
  benchShim = pkgs.writeShellApplication {
    name = "bench";
    text = ''exec ${bakeoffBench}/bin/bakeoff-bench devenv bench "$@"'';
  };
in
{
  # devhobby: the ERPNext MCP binary + provision unit (devpro's defaults are overridden per
  # call by bakeoff-pi / the provision command below via ERPNEXT_MCP_* env vars).
  ompDevAgent.erpnext.enable = true;

  environment.systemPackages = [
    piPkg dshPkg benchShim bakeoffBench repoAt (runner "pi") (runner "dsh")
    mariadb pkgs.redis pkgs.jq pkgs.curl pkgs.git pkgs.python3 pkgs.nodejs pkgs.yarn
    # Frappe's get_pdf (pdfkit) needs it; roster mail tests attach real PDFs (H9, 11-05).
    pkgs.wkhtmltopdf
  ];

  services.mysql = {
    enable = true;
    package = mariadb;
    settings.mysqld = {
      bind-address = "127.0.0.1";
      character-set-server = "utf8mb4";
      collation-server = "utf8mb4_unicode_ci";
      skip-character-set-client-handshake = true;
      innodb_buffer_pool_size = "2G";
      # Throwaway test data: trade durability for reset speed.
      innodb_flush_log_at_trx_commit = 2;
      innodb_doublewrite = 0;
    };
  };

  systemd.tmpfiles.rules = [
    "d /var/lib/bakeoff 0755 root root -"
    "d ${home}/bakeoff 0755 cristian users -"
    "d ${home}/bakeoff/runs 0755 cristian users -"
    "d ${home}/bakeoff/runs/interactive 0755 cristian users -"
    "d ${home}/bakeoff/work 0755 cristian users -"
    "d ${home}/bakeoff/secrets 0700 cristian users -"
  ];

  systemd.services.bakeoff-proxy = {
    description = "Bake-off recording proxy (Melious + local Qwen + web MCP; spend-capped)";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    environment = {
      BAKEOFF_ROOT = "${home}/bakeoff";
      BAKEOFF_SPEND_CAP = "200";
      BAKEOFF_MAX_TOKENS = "32768";
      BAKEOFF_HOMELAB_URL = "http://192.168.40.28:9000";
      BAKEOFF_WEBMCP_URL = "http://192.168.40.28:9542";
      BAKEOFF_SAMPLING = builtins.toJSON (builtins.listToAttrs
        (map (m: { name = m.id; value = m.sampling; }) (melious ++ homelab)));
    };
    serviceConfig = {
      User = "cristian";
      Group = "users";
      ExecStart = "${py}/bin/python3 -I ${proxyPy}";
      LoadCredential = [
        "melious:/var/lib/melious.key"
        "webmcp:/var/lib/bakeoff/web-mcp.token"
      ];
      Restart = "always";
      RestartSec = 3;
      NoNewPrivileges = true;
      PrivateTmp = true;
      ProtectSystem = "strict";
      ReadWritePaths = [ "${home}/bakeoff/runs" "${home}/bakeoff/ledger" ];
    };
    unitConfig.ConditionPathExists = "/var/lib/melious.key";
  };
  # ReadWritePaths needs the ledger dir to exist before the unit starts.
  systemd.tmpfiles.settings."10-bakeoff-ledger"."${home}/bakeoff/ledger".d = {
    user = "cristian"; group = "users"; mode = "0755";
  };
  # Restart the proxy when either secret changes (key rotation = reinstall the file).
  systemd.paths.bakeoff-proxy-secrets = {
    wantedBy = [ "multi-user.target" ];
    pathConfig.PathChanged = [ "/var/lib/melious.key" "/var/lib/bakeoff/web-mcp.token" ];
  };
  systemd.services.bakeoff-proxy-secrets = {
    serviceConfig.Type = "oneshot";
    script = "systemctl try-restart bakeoff-proxy.service || systemctl start bakeoff-proxy.service";
  };

  # Local SMTP sink for the bench sites (like devpro's Mailpit on 127.0.0.1:1026): the roster's
  # mail-intent tests need a real outgoing account that accepts mail (H3/H5/H9, 11-05).
  # Loopback only; UI http://127.0.0.1:8026 via ssh tunnel.
  systemd.services.bakeoff-mailpit = {
    description = "Bake-off local SMTP sink (Mailpit) for the Frappe benches";
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      User = "cristian";
      Group = "users";
      ExecStart = "${pkgs.mailpit}/bin/mailpit --smtp 127.0.0.1:1026 --listen 127.0.0.1:8026 --smtp-auth-accept-any --smtp-auth-allow-insecure --max 20000";
      Restart = "always";
      PrivateTmp = true;
      NoNewPrivileges = true;
    };
  };

  # Interactive pi config (the runs get their own copy under ~/bakeoff/runs/<id>/agent).
  # Always re-rendered: it holds no hand-made state and no secret.
  systemd.user.services.pi-models-seed = {
    description = "Render ~/.pi/agent/{models,settings,mcp}.json (bake-off providers via bakeoff-proxy)";
    wantedBy = [ "default.target" ];
    serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
    path = [ pkgs.coreutils pkgs.gnused pkgs.jq ];
    script = ''
      d=$HOME/.pi/agent
      mkdir -p "$d" "$HOME/bakeoff/runs/interactive"
      sed 's#@RUN@#interactive#g' ${modelsTemplate} > "$d/models.json.tmp" && mv "$d/models.json.tmp" "$d/models.json"
      jq --arg tp ${taskplanePkgDir} '. + {packages: [$tp]}' ${piSettings} > "$d/settings.json"
      jq -n '{mcpServers: {
        context7: {command: "/run/current-system/sw/bin/context7-mcp", args: [], exposure: "direct"},
        web: {url: "http://127.0.0.1:5099/r/interactive/webmcp/mcp", exposure: "hidden",
              toolExposure: {"searxng__*": "direct"}}}}' > "$d/mcp.json"
    '';
    restartTriggers = [ modelsTemplate piSettings ];
  };

  # One template unit per bench: `bakeoff-bench init N` enables bakeoff-bench@N.
  systemd.user.services."bakeoff-bench@" = {
    description = "Bake-off Frappe bench b%i";
    unitConfig.ConditionPathExists = "${home}/bakeoff/benches/b%i/Procfile";
    after = [ "mysql.service" ];
    path = [ "/run/current-system/sw" ];
    serviceConfig = {
      Type = "simple";
      ExecStart = "${bakeoffBench}/bin/bakeoff-bench run %i bench start";
      Restart = "on-failure";
      RestartSec = 5;
      KillMode = "mixed";
      TimeoutStopSec = 30;
      Environment = "PYTHONUNBUFFERED=1";
    };
  };
  # Instances come up at login/boot when their bench exists (ConditionPathExists above).
  systemd.user.targets.default.wants = map (n: "bakeoff-bench@${toString n}.service") [ 1 2 3 ];

  # ERPNext MCP provisioning against the bake-off benches (not devpro's default paths).
  systemd.user.services.bakeoff-erpnext-mcp-provision = {
    description = "ERPNext MCP: API user + keys on every bake-off bench site";
    path = [ "/run/current-system/sw" ];
    environment = {
      ERPNEXT_WORKSPACE = "${home}/bakeoff/erpnext";
      ERPNEXT_MCP_BENCHES = "${home}/bakeoff/benches";
      ERPNEXT_MCP_CREDENTIALS = "${home}/bakeoff/secrets/erpnext-mcp.json";
      ERPNEXT_MCP_READONLY_BENCHES = "";
      DIRENV = "/run/current-system/sw/bin/direnv";
    };
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "/run/current-system/sw/bin/erpnext-mcp-provision";
      TimeoutStartSec = "30min";
    };
  };
}
