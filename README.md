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
- **Pi production worker pool** — `modules/pi-dev-agent.nix` on devpro. Pi is
  Nix-packaged (`pkgs/pi-coding-agent`) and exposes exactly two allowlisted models:
  local `strata/qwen3.8-flash` (default, one global lane) and the bake-off-verified
  Melious `deepseek-v4.1-flash-1017`. Every batch includes exactly one local lane;
  explicitly approved external lanes use persistent live cap/worker controls and
  stable worker IDs, with each worker in its own TUIOS worktree.
  It has Context7, exclusive test-bench ERPNext, and session-scoped read-only TUIOS
  MCPs. Full policy and reviewer workflow: [docs/pi-task-production.md](docs/pi-task-production.md).
  A single local implementation slice looks like:
  `pi-task --cwd ~/development/project --allow src/file.py --allow tests/test_file.py --success 'pytest -q tests/test_file.py' 'Implement the approved OpenSpec slice'`.
  The wrapper requires an exact file allowlist and success command, caps the whole run
  at 6 GiB, exposes only the project in a minimal-home bubblewrap sandbox (`.git` is
  read-only and `/var` is empty), and refuses HEAD/index movement or any changed
  Git-visible path outside the allowlist. Pi never commits; the wrapper reports both
  command statuses and the final diff stat.
- **Melious key** (per VM, user-installed once):
  `ssh devhobby 'sudo install -m 600 /dev/stdin /var/lib/melious.key' <<< "sk-..."`
  `omp-models-seed.service` renders `~/.omp/agent/models.yml` from it (re-runs by itself
  when the key file changes). Default model `melious/glm-5.3-flash`.
- **omp-task** (`modules/omp-dev-agent.nix`, devhobby only) — retained for bake-off
  reproducibility. It is disabled on devpro, where Pi replaced the Melious-backed path:
  `omp-task --cwd ~/development/<proj> "<spec>\nAcceptance: <cmd>"` (default cap 0.50 USD).
  bwrap: RW only the project dir, `$HOME` is empty, `/var` hidden, `.git` read-only.
- **Bake-off harness, devhobby only** (`modules/bakeoff`, 2026-11-05): pi 1.5.0 + taskplane
  0.30.6 (`pkgs/pi-coding-agent`), dsh (`pkgs/dsh`, on the official Node binary,
  `pkgs/nodejs-official`), frappe-bench CLI (`pkgs/frappe-bench`), and these tools:
  - `bakeoff-proxy` on 127.0.0.1:5099 is the only path to models. It holds the Melious key,
    records every call, and enforces a total cap of 200 credits.
  - `bakeoff-pi` / `bakeoff-dsh` run one sandboxed, recorded agent run each. `--orch` drives a
    taskplane batch.
  - `bakeoff-bench init|reset|link|mail|snapshot` manages throwaway Frappe benches b1–b3,
    backed by a local MariaDB and Mailpit.
  - `bakeoff-repo-at` creates base-only run repos.

  Data lives in `~/bakeoff`, not in this repo. Secrets: `/var/lib/melious.key` and
  `/var/lib/bakeoff/web-mcp.token`.
- **MCPs** — `context7-mcp` (Nix-packaged, `pkgs/context7-mcp`) on both VMs; `erpnext-mcp`
  (`modules/erpnext-mcp`, test :8000 / UAT :8001 only, no delete) on devpro. Wired into
  `~/.codex/config.toml` and `~/development/erpnext/.mcp.json` by `omp-dev-integrations`.
  ERPNext creds (user `mcp-dev`), devpro only:
  `ssh devpro 'sudo install -m 600 -o cristian -g users /dev/stdin /var/lib/erpnext-mcp.env' <<< $'ERPNEXT_TEST_KEY=…\nERPNEXT_TEST_SECRET=…\nERPNEXT_UAT_KEY=…\nERPNEXT_UAT_SECRET=…'`
