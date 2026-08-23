{ pkgs, lib, ... }:

# Coding agents: pi (earendil-works, nix-packaged FOD in ../pkgs) + oh-my-pi.
#
# oh-my-pi is deliberately NOT nix-packaged: it is a pi EXTENSION, installed by pi's
# own package manager into ~/.pi/agent/npm/ (persistent /home volume) and registered
# in ~/.pi/agent/settings.json. Its standalone bin is TS-annotated and only runs
# under pi's loader anyway. The oneshot below self-installs it on first boot.
#
# Model backend is Melious (per-VM API key). The key is NOT in the nix store or this
# repo: the user drops it at /var/lib/melious.key (persistent var volume) from a
# laptop, once per VM:
#
#   ssh devhobby 'sudo install -m 600 /dev/stdin /var/lib/melious.key' <<< "sk-..."
#
# pi-models-seed renders ~cristian/.pi/agent/models.json from it on boot — only if
# the file is missing, so hand edits survive. Delete models.json + restart the
# service (or reboot) to re-render after a key rotation.
let
  pi = pkgs.callPackage ../pkgs/pi-coding-agent { };
  openspec = pkgs.callPackage ../pkgs/openspec { };
in
{
  environment.systemPackages = [ pi openspec ];

  systemd.services.pi-models-seed = {
    description = "Seed pi models.json from /var/lib/melious.key";
    wantedBy = [ "multi-user.target" ];
    after = [ "local-fs.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
      cfg=/home/cristian/.pi/agent/models.json
      key_file=/var/lib/melious.key
      if [ -f "$cfg" ]; then
        exit 0
      fi
      if [ ! -f "$key_file" ]; then
        echo "no $key_file yet — install the per-VM Melious key to enable pi (see modules/agents.nix)"
        exit 0
      fi
      key=$(cat "$key_file")
      mkdir -p /home/cristian/.pi/agent
      cat > "$cfg" <<EOF
      {
        "providers": {
          "melious": {
            "baseUrl": "https://api.melious.ai/v1",
            "api": "openai-completions",
            "apiKey": "$key",
            "compat": { "supportsDeveloperRole": false, "supportsReasoningEffort": false },
            "models": [
              { "id": "glm-5.2", "contextWindow": 327680 },
              { "id": "qwen3.6-35b-a3b", "contextWindow": 262144 }
            ]
          }
        }
      }
      EOF
      chown -R cristian:users /home/cristian/.pi
      chmod 600 "$cfg"
    '';
  };

  # Self-install the oh-my-pi extension into cristian's pi on first boot (idempotent:
  # skips once settings.json lists it). Runs as the user so ownership is right, needs
  # the network for the npm fetch.
  systemd.services.oh-my-pi-install = {
    description = "Install oh-my-pi extension into pi (per-user, persistent /home)";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" "pi-models-seed.service" ];
    wants = [ "network-online.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      User = "cristian";
      Group = "users";
      Environment = "HOME=/home/cristian";
    };
    path = [ pi pkgs.nodejs ];
    script = ''
      settings=/home/cristian/.pi/agent/settings.json
      if [ -f "$settings" ] && grep -q "npm:oh-my-pi" "$settings"; then
        exit 0
      fi
      pi install npm:oh-my-pi
    '';
  };
}
