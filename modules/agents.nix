{ pkgs, ompPkg, herdrPkg, ... }:

let
  openspec = pkgs.callPackage ../pkgs/openspec { };

  # codex (OpenAI's Rust coding agent), pinned to the latest upstream release —
  # same recipe as cdssrv02's modules/system/base.nix, kept here so the VMs move
  # independently of the host's bump cycle. nixpkgs ships an old 0.92.0 and
  # upstream releases every few days.
  #
  # The npm package @openai/codex is a JS shim that exec's a precompiled,
  # *statically linked* (musl) Rust binary shipped in @openai/codex@<VER>-linux-x64,
  # so fetch that tarball directly: no npm, no compile, no patchelf. The tarball
  # also bundles the ripgrep codex shells out to.
  #
  # To bump: change `codexVersion`, then update the hash with:
  #   nix store prefetch-file --json \
  #     "https://registry.npmjs.org/@openai/codex/-/codex-<VER>-linux-x64.tgz"
  codexVersion = "0.144.1";
  codexPinned = pkgs.stdenvNoCC.mkDerivation {
    pname = "codex";
    version = codexVersion;
    src = pkgs.fetchurl {
      url = "https://registry.npmjs.org/@openai/codex/-/codex-${codexVersion}-linux-x64.tgz";
      hash = "sha256-4qZNQhwQqvC348DovXG3Hkl9dYIwAzGLZ0onjXGt0Mc=";
    };
    sourceRoot = "package";            # tarball top-level dir
    dontConfigure = true;
    dontBuild = true;
    dontPatchELF = true;               # binary is statically linked (musl)
    installPhase = ''
      runHook preInstall
      vendor=vendor/x86_64-unknown-linux-musl
      install -Dm755 "$vendor/bin/codex" "$out/bin/codex"
      install -Dm755 "$vendor/codex-path/rg" "$out/bin/rg-codex" || true
      runHook postInstall
    '';
    meta = {
      description = "OpenAI Codex CLI (Rust), pinned to latest precompiled release";
      homepage = "https://github.com/openai/codex";
      mainProgram = "codex";
      platforms = [ "x86_64-linux" ];
    };
  };

  # herdr config. Read at startup and on `herdr server reload-config`; client
  # and server share this one file.
  herdrConfigToml = pkgs.writeText "herdr-config.toml" ''
    # Managed by modules/agents.nix — do not edit in place; edit the Nix module
    # and redeploy, then: systemctl --user restart herdr

    [update]
    # Version is pinned by the Nix flake input, so a background check can't lead
    # to a usable upgrade here — and `herdr update` MUST NOT be run: it would
    # download a binary over the Nix store path and be reverted on next rebuild.
    # Bump via the flake input, in every repo that pins herdr.
    channel = "stable"
    version_check = false
    manifest_check = false

    [ui]
    show_agent_labels_on_pane_borders = true

    [ui.toast]
    delivery = "herdr"

    [experimental]
    # Restore panes' scrollback across a server restart. Upstream files this
    # under [experimental], NOT [server] — under the wrong table it parses fine
    # and is silently ignored.
    pane_history = true
  '';

  # claude-code, pinned to the latest upstream release ahead of nixpkgs (which
  # carries 2.1.140 here — days-to-weeks behind downloads.claude.ai). Same
  # override pattern as cdssrv02's modules/system/base.nix; kept in this repo so
  # the VMs can move independently of the host's bump cycle.
  #
  # To bump: change `claudeCodeVersion`, then get the hash with
  #   nix store prefetch-file --json \
  #     "https://downloads.claude.ai/claude-code-releases/<VER>/linux-x64/claude"
  # Drop the override once nixpkgs stable catches up past this version.
  claudeCodeVersion = "2.1.241";
  claudeCodePinned = pkgs.claude-code.overrideAttrs (_: {
    version = claudeCodeVersion;
    src = pkgs.fetchurl {
      url = "https://downloads.claude.ai/claude-code-releases/${claudeCodeVersion}/linux-x64/claude";
      hash = "sha256-B3G9hmz/grdlgfwEmfZSnho2hFB48UT4yB3Ms7xwN7g=";
    };
  });
in

# Coding agent: omp (github:can1357/oh-my-pi, flake input) — Can Bölük's fork of
# @mariozechner's pi, standalone binary. Replaced pi + the npm "oh-my-pi" extension
# 2026-08-23: the unscoped npm name turned out to be an unrelated third-party
# extension riding the real project's name (its linked repo 404s); the genuine
# oh-my-pi is the scoped @oh-my-pi/* scope / the omp binary, and being a plain
# package it needs no boot-time npm install service at all.
#
# Model backend is Melious (per-VM API key). The key is NOT in the nix store or this
# repo: the user drops it at /var/lib/melious.key (persistent var volume) from a
# laptop, once per VM:
#
#   ssh devhobby 'sudo install -m 600 /dev/stdin /var/lib/melious.key' <<< "sk-..."
#
# omp-models-seed renders ~cristian/.omp/agent/{models,config}.yml from it on boot —
# only if models.yml is missing, so hand edits survive. Delete models.yml + restart
# the service (or reboot) to re-render after a key rotation.
{
  # Three agents on purpose: omp (Melious-backed, per-VM key), claude-code and
  # codex (each an upstream account — the user runs `claude` / `codex` and
  # authenticates interactively; no credential is baked into the VM image).
  environment.systemPackages = [ ompPkg openspec claudeCodePinned codexPinned herdrPkg ];

  # The user manager must run without an active login, or the herdr session dies
  # on logout and never returns at boot. devpro already gets this from
  # erpnext-uat.nix, but that module is devpro-only — assert it here so devhobby
  # gets it too (same value, so the two definitions agree).
  users.users.cristian.linger = true;

  # `codex app-server daemon bootstrap --remote-control` installs and updates a
  # paired standalone Codex under $HOME, but its PID backend has no native
  # systemd unit. Start that managed binary at user-manager boot so pairing is
  # available before the first SSH client connects. The service is harmless on
  # a fresh VM before bootstrap has created the standalone installation.
  systemd.user.services.codex-app-server = {
    description = "Paired Codex app-server daemon";
    wantedBy = [ "default.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
      managed="$HOME/.codex/packages/standalone/current/codex"
      if [ -x "$managed" ]; then
        "$managed" app-server daemon start
      fi
    '';
    preStop = ''
      managed="$HOME/.codex/packages/standalone/current/codex"
      if [ -x "$managed" ]; then
        "$managed" app-server daemon stop
      fi
    '';
  };

  # herdr headless server, per user. Same shape as cdssrv02's
  # modules/services/herdr-server.nix — see that file for the full rationale.
  # Short version: `herdr --remote <target>` has no user/sudo flag, so the
  # account is whatever the ssh target resolves to and the socket it needs is
  # mode 0700 under that account's $HOME. cristian is the account the laptop
  # keys are authorized for, so the server runs as a systemd USER service with
  # lingering on (asserted just above) — the workspace must outlive the ssh
  # login that started it and come back at boot.
  #
  # Do NOT set XDG_RUNTIME_DIR: herdr derives its socket path from $HOME and an
  # interactive shell must land on the same path or `herdr status` won't see it.
  systemd.user.services.herdr = {
    description = "herdr headless server (agent workspace, UNIX socket)";
    documentation = [ "https://github.com/herdrdev/herdr" ];
    wantedBy = [ "default.target" ];

    preStart = ''
      install -d -m 0700 "$HOME/.config/herdr"
      install -m 0600 ${herdrConfigToml} "$HOME/.config/herdr/config.toml"
    '';

    serviceConfig = {
      Type = "simple";
      ExecStart = "${herdrPkg}/bin/herdr server";
      # Graceful stop persists session.json (workspaces/tabs/cwds); SIGKILL
      # would lose the layout, so give it room to write.
      ExecStop = "${herdrPkg}/bin/herdr server stop";
      TimeoutStopSec = 30;
      KillSignal = "SIGTERM";
      Restart = "on-failure";
      RestartSec = 5;
      # Panes spawn shells and long-running agents; don't reap them as strays
      # when the main process restarts.
      KillMode = "mixed";
    };

    environment.TERM = "xterm-256color";
    path = with pkgs; [ bashInteractive git openssh ];
  };

  # Agent integrations are per-user hook FILES under $HOME, not packages, and
  # `herdr integration install` writes them imperatively — so a rebuilt or
  # re-provisioned VM would silently come up without them. Install them from a
  # user service instead, so they are part of the machine definition.
  #
  # The command is idempotent (re-running reports "current") and merges into
  # existing configs rather than overwriting: it appends a SessionStart hook to
  # ~/.claude/settings.json, ~/.codex/hooks.json and omp's extensions dir,
  # leaving unrelated settings alone. Hooks are versioned (v8 at time of
  # writing), so this also upgrades them after a herdr bump.
  systemd.user.services.herdr-integrations = {
    description = "Install herdr agent integrations (omp, claude, codex)";
    wantedBy = [ "default.target" ];
    after = [ "herdr.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    path = [ herdrPkg pkgs.coreutils ];
    script = ''
      # Each integration refuses to install unless the agent's config dir
      # already exists — normally created on that agent's first interactive run.
      # On a freshly provisioned VM nobody has run claude or codex yet, so the
      # hooks would be missing until someone did (omp gets its dir from
      # omp-models-seed, which is why it alone installed on first boot).
      # Create the dirs up front so the integrations land at boot instead. This
      # only makes empty directories; no agent config or credential is written.
      mkdir -p "$HOME/.claude" "$HOME/.codex"

      # Still tolerate a refusal rather than failing the unit: upstream may add
      # further preconditions, and a missing hook must not block the others.
      for agent in omp claude codex; do
        herdr integration install "$agent" || \
          echo "herdr: $agent integration not installed (run $agent once, then: systemctl --user restart herdr-integrations)"
      done
    '';
  };

  systemd.services.omp-models-seed = {
    description = "Seed omp models.yml/config.yml from /var/lib/melious.key";
    wantedBy = [ "multi-user.target" ];
    after = [ "local-fs.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
      dir=/home/cristian/.omp/agent
      key_file=/var/lib/melious.key
      if [ -f "$dir/models.yml" ]; then
        exit 0
      fi
      if [ ! -f "$key_file" ]; then
        echo "no $key_file yet — install the per-VM Melious key to enable omp (see modules/agents.nix)"
        exit 0
      fi
      key=$(cat "$key_file")
      mkdir -p "$dir"
      cat > "$dir/models.yml" <<EOF
      providers:
        melious:
          baseUrl: https://api.melious.ai/v1
          api: openai-completions
          apiKey: $key
          models:
            - id: glm-5.2
              contextWindow: 327680
            - id: qwen3.6-35b-a3b
              contextWindow: 262144
      EOF
      if [ ! -f "$dir/config.yml" ]; then
        cat > "$dir/config.yml" <<EOF
      modelRoles:
        default: melious/glm-5.2
      EOF
      fi
      chown -R cristian:users /home/cristian/.omp
      chmod 600 "$dir/models.yml"
    '';
  };
}
