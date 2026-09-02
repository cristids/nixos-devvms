# nixos-devvms

NixOS configs for the dev VM jails (`devpro`, `devhobby`) hosted on **cdssrv02** via
libvirt. Same layout as [nixos-laptops](https://github.com/cristids/nixos-laptops):
`hosts/` split by machine, shared `modules/`, local `pkgs/`.

| VM | IP | Purpose |
|----|-----|---------|
| devpro | 192.168.40.26 | professional work (scoped work creds, driven with care) |
| devhobby | 192.168.40.27 | hobby projects / agent playground |

## How these deploy

The VMs are full NixOS guests. `cdssrv02` owns their libvirt domain definitions, while
this repo owns the guest systems. Deploy each configuration directly to its guest:

```sh
cd /home/cristian/cristids/nixos-devvms
nixos-rebuild switch --flake .#devpro --target-host devpro --sudo
nixos-rebuild switch --flake .#devhobby --target-host devhobby --sudo
```

Laptops and the `cdssrv02` management keys can ssh into the VMs. Anything per-user
(Melious key, `gh auth login`, private emacs-config clones) is installed explicitly
and lands on the persistent `/home` / `/var` volumes.

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
