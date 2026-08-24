{ pkgs, ompPkg, ... }:

let
  openspec = pkgs.callPackage ../pkgs/openspec { };

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
  # Two agents on purpose: omp (Melious-backed, per-VM key) and claude-code
  # (Anthropic account, user runs `claude` and authenticates interactively —
  # no credential is baked into the VM image).
  environment.systemPackages = [ ompPkg openspec claudeCodePinned ];

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
