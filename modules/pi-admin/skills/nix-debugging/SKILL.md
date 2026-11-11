---
name: nix-debugging
description: Diagnose Nix evaluation and build problems in the devpro flake (nixos-devvms) - reading eval errors and traces, inspecting option values, why a package is in the closure, and what changed between two system generations. Use when nix build or nixos-rebuild fails or when a closure or unit change is unexpected.
---

# Nix debugging (devpro, nixos-devvms)

Run from /home/cristian/development/nixos-devvms. The flake output is
`nixosConfigurations.devpro`.

## Evaluation errors
- Rebuild with context: `nix build .#nixosConfigurations.devpro.config.system.build.toplevel --show-trace`.
  Read the trace bottom-up; the first frame in a repo file is usually the cause.
- "path ... does not exist" / file missing → new file not `git add`ed (flakes only see tracked files).
- "Permission denied" opening a repo file → a tracked file is not world-readable (fix to 644/755).
- Inspect a value: `nix eval .#nixosConfigurations.devpro.config.services.foo.enable`
  or `nix eval --json .#nixosConfigurations.devpro.config.systemd.services.foo.serviceConfig`.
- Where an option is defined: `nix eval .#nixosConfigurations.devpro.options.services.foo.enable.files`.
- Package version in this nixpkgs: `nix eval --raw .#nixosConfigurations.devpro.pkgs.<name>.version`.
- Interactive: `nix repl` then `:lf .` and explore `nixosConfigurations.devpro.config`.

## Build failures
- Full log: `nix log /nix/store/<drv>.drv` (the failing .drv is in the error).
- FOD "hash mismatch": an upstream fetch changed; paste the "got:" hash only after
  checking that the version/spec is the intended one.
- Keep a failed build tree: `nix build ... --keep-failed` (path printed on failure).

## Closure questions
- Why is X in the system: `nix why-depends --precise ./result /nix/store/<X>` (or `.#…toplevel`).
- Size: `nix path-info -Sh ./result`; biggest deps: `nix path-info -rsSh ./result | sort -k2 -h | tail`.
- What changed vs. the running system:
  `nix store diff-closures /run/current-system ./result`
  (use `nvd diff` if installed). Deploys run from cdssrv02 (never `nixos-rebuild` here); ask the user for the
  dry-activate unit-level view.
- Generations: `nixos-rebuild list-generations`; previous system under `/nix/var/nix/profiles/system-*-link`.

## Habits
- Build `--no-link` (or remove `./result` after) so stray GC roots do not pile up.
- Do not run `nix flake update` / bump inputs as part of debugging; input moves
  are the update jobs' work.
