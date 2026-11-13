{
  description = "Dev VM guests (devpro/devhobby) hosted by libvirt on cdssrv02";

  # Same layout idea as nixos-laptops: hosts/ + modules/ + pkgs/, one flake, split by
  # machine. The initial raw EFI images are written to libvirt-backed ZFS zvols;
  # subsequent deployments target each full NixOS guest directly. cdssrv02 owns
  # only their libvirt domains.
  inputs = {
    # Keep both guests on the same stable NixOS release.
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

    # omp (can1357/oh-my-pi) — THE oh-my-pi, Can Bölük's fork of pi. Deliberately NO
    # nixpkgs follows: its build (rust-overlay + bun2nix) is pinned to its own inputs
    # and forcing our stable nixpkgs under it would just break the from-source build.
    omp.url = "github:can1357/oh-my-pi";

    # herdr — terminal workspace manager for AI coding agents, run as a headless
    # per-user server so agent panes survive the ssh login that started them
    # (see modules/agents.nix). Attach from a laptop with `herdr --remote devpro`.
    #
    # This input MUST match cdssrv02's (/etc/nixos) and
    # nixos-laptops' — client and server speak a versioned protocol and refuse to
    # attach across incompatible versions. Since v0.9.0, upstream's default branch
    # carries the normal stable release. flake.lock keeps each deployment
    # reproducible while explicit updates advance all three repos together.
    #
    # Repo moved ogulcancelik/herdr -> herdrdev/herdr 2026-08-28 (org transfer,
    # 33.2k stars, AGPL-3.0). The old path redirects; pin the real one.
    herdr = {
      url = "github:herdrdev/herdr/v0.9.5";
    };

    # tuios — terminal WM daemon (modules/tuios.nix). LOCKSTEP with
    # nixos_llm_srv and nixos-laptops (laptops attach over a versioned link
    # protocol): same tag in all three, no nixpkgs follows, so upstream's own
    # pinned nixpkgs builds it and every host gets the identical store path.
    # Bumped in all three repos at once by lockstep-update-auto (cdssrv02).
    tuios.url = "github:Gaurav-Gosain/tuios/v0.12.1";

    # pi-admin (cristids/pi-admin, PRIVATE): pi + sysadmin gate, one project
    # deployed on devpro and cdssrv02. This repo is built on cdssrv02, which
    # fetches it with a read-only deploy key via the `github-pi-admin` ssh
    # alias (nixos_llm_srv modules/ai-services/pi-admin). Keep nixos_llm_srv on
    # the same rev.
    pi-admin.url = "git+ssh://git@github-pi-admin/cristids/pi-admin?ref=main";

    # Emacs 31 for the VMs, same two-piece recipe as nixos-laptops: emacs-overlay
    # gives `emacs-unstable-nox', and the emacs-31 release branch is swapped in as
    # its src (overlays/emacs-31-branch.nix) because emacs-overlay's unstable
    # tracks pretest tags only and skipped 31.1-rc1. Costs the cache.nixos.org
    # substitute the old emacs30-nox had. Drop both inputs once nixpkgs stable
    # carries emacs31.
    emacs-overlay = {
      url = "github:nix-community/emacs-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    emacs-31 = {
      url = "github:emacs-mirror/emacs/emacs-31";
      flake = false;
    };

    # tramp-rpc (ArthurHeymans/emacs-tramp-rpc) — fast TRAMP backend: laptop
    # Emacs speaks MessagePack-RPC to a small Rust server on the guest instead
    # of parsing shell output. The overlay provides `emacs-tramp-rpc-server`
    # (the binary, installed on devhobby at /run/current-system/sw/bin/
    # tramp-rpc-server — the client's documented NixOS path) and injects
    # `tramp-rpc` into the emacsPackages scope. Server preinstall beats the
    # client's ~/.cache auto-deploy here because VM reprovisions wipe home.
    # Client side lives in nixos-laptops (same input) + the mini emacs config
    # (`/rpc:devhobby:` paths). No nixpkgs follows: builds with our stable
    # rustPlatform via the overlay's callPackage, its own inputs are only
    # used for its CI cross-builds.
    # Pinned to a release tag in lockstep with cdssrv02 and nixos-laptops.
    emacs-tramp-rpc.url = "github:ArthurHeymans/emacs-tramp-rpc/v0.16.0";
  };

  outputs = { self, nixpkgs, omp, herdr, tuios, emacs-overlay, emacs-31, emacs-tramp-rpc, pi-admin }:
    let
      system = "x86_64-linux";
      mkVM = host: nixpkgs.lib.nixosSystem {
        inherit system;
        specialArgs = {
          ompPkg = omp.packages.${system}.default;
          herdrPkg = herdr.packages.${system}.default;
          tuiosPkg = tuios.packages.${system}.default;
          piAdminLean = pi-admin.lib.leanCoding; # lean-coding skill, single source (pi-admin + pi-task)
        };
        modules = [
          # Guests deliberately run the stock nixpkgs kernel so their system closure
          # substitutes from cache.nixos.org instead of rebuilding a custom kernel.
          { nixpkgs.overlays = [
              emacs-overlay.overlays.default
              (import ./overlays/emacs-31-branch.nix emacs-31)
              emacs-tramp-rpc.overlays.default
              # Last: leaf packages devvm-update-auto held back on an older
              # nixpkgs because they stopped building (auto-pins.json).
              (import ./overlays/auto-pins.nix)
            ]; }
          pi-admin.nixosModules.default # off unless services.pi-admin.enable (devpro)
          ./hosts/common
          (./hosts + "/${host}")
        ];
      };
    in {
      nixosConfigurations = {
        devpro = mkVM "devpro";
        devhobby = mkVM "devhobby";
      };

      # Exposed for hash-bump iteration / tests: `nix build path:.#openspec`.
      packages.${system} = {
        openspec =
          nixpkgs.legacyPackages.${system}.callPackage ./pkgs/openspec { };
        context7-mcp =
          nixpkgs.legacyPackages.${system}.callPackage ./pkgs/context7-mcp { };
        # Build runs the pytest suite: `nix build path:.#erpnext-mcp`.
        erpnext-mcp =
          nixpkgs.legacyPackages.${system}.callPackage ./modules/erpnext-mcp { };
        # Bake-off (devhobby): pi 1.5 + taskplane, dsh, frappe-bench CLI.
        pi-coding-agent =
          nixpkgs.legacyPackages.${system}.callPackage ./pkgs/pi-coding-agent { };
        dsh = nixpkgs.legacyPackages.${system}.callPackage ./pkgs/dsh { };
        frappe-bench =
          nixpkgs.legacyPackages.${system}.callPackage ./pkgs/frappe-bench { };
      };
    };
}
