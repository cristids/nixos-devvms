# nixos-devvms

NixOS configs for the dev VM jails (`devpro`, `devhobby`) hosted on **cdssrv03** via
microvm.nix. Same layout as [nixos-laptops](https://github.com/cristids/nixos-laptops):
`hosts/` split by machine, shared `modules/`, local `pkgs/`.

| VM | IP | Purpose |
|----|-----|---------|
| devpro | 192.168.40.108 | professional work (scoped work creds, driven with care) |
| devhobby | 192.168.40.109 | hobby projects / agent playground |

## How these deploy

The VMs never deploy themselves. cdssrv03's flake consumes this repo as input `devvms`;
the host builds the guest closures and shares them read-only via virtiofs. Ship a change:

```sh
# from cdssrv02
cd /root/nixos-devvms && git commit && git push
cd /etc/nixos/cdssrv03 && nix flake update devvms
nixos-rebuild switch --flake /etc/nixos/cdssrv03#cdssrv03 --target-host root@cdssrv03
ssh root@cdssrv03 systemctl restart microvm@devpro microvm@devhobby
```

Only laptops (personal-device keys) can ssh INTO the VMs. The host cannot; cdssrv02
cannot. Anything per-user (Melious key, `gh auth login`, private emacs-config clones)
is done by the user from a laptop and lands on the persistent `/home` / `/var` volumes.

## What's inside

- **emacs** — `emacs30-nox` with all tree-sitter grammars baked in + LSPs/toolchain
  (`modules/emacs.nix`). Personal distro configs: clone manually after `gh auth login`.
- **devenv + direnv/nix-direnv, gh, uv, python** — `modules/dev-tooling.nix`.
- **pi + oh-my-pi** — `modules/agents.nix`. pi is nix-packaged (`pkgs/pi-coding-agent`,
  FOD; bump = version + 2 hashes). oh-my-pi self-installs as a pi extension into
  `~/.pi/agent/npm/` on first boot (`oh-my-pi-install.service`).
- **Melious key** (per VM, user-installed once):
  `ssh devhobby 'sudo install -m 600 /dev/stdin /var/lib/melious.key' <<< "sk-..."`
  `pi-models-seed.service` renders `~/.pi/agent/models.json` from it on next boot.
