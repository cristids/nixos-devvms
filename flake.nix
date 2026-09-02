{
  description = "Dev VM guests (devpro/devhobby) hosted by libvirt on cdssrv02";

  # Same layout idea as nixos-laptops: hosts/ + modules/ + pkgs/, one flake, split by
  # machine. The initial raw EFI images are written to libvirt-backed ZFS zvols;
  # subsequent deployments target each full NixOS guest directly. cdssrv02 owns
  # only their libvirt domains.
  inputs = {
    # Keep both guests on the same stable NixOS release.
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";

    # omp (can1357/oh-my-pi) — THE oh-my-pi, Can Bölük's fork of pi. Deliberately NO
    # nixpkgs follows: its build (rust-overlay + bun2nix) is pinned to its own inputs
    # and forcing our 25.11 under it would just break the from-source build.
    omp.url = "github:can1357/oh-my-pi";

    # herdr — terminal workspace manager for AI coding agents, run as a headless
    # per-user server so agent panes survive the ssh login that started them
    # (see modules/agents.nix). Attach from a laptop with `herdr --remote devpro`.
    #
    # Pinned to the v0.8.2 TAG, not the branch: untagged HEAD also reports version
    # 0.8.2, so branch-tracking would drift the client/server protocol with no
    # version string to warn us. This pin MUST match cdssrv02's (/etc/nixos) and
    # nixos-laptops' — client and server speak a versioned protocol and refuse to
    # attach across incompatible versions (0.7.4 = protocol 17, 0.8.2 = 20).
    #
    # Repo moved ogulcancelik/herdr -> herdrdev/herdr 2026-08-28 (org transfer,
    # 33.2k stars, AGPL-3.0). The old path redirects; pin the real one.
    herdr = {
      url = "github:herdrdev/herdr/v0.8.2";
      inputs.nixpkgs.follows = "nixpkgs";
    };

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
    # (`/rpc:devhobby:` paths). No nixpkgs follows: builds with our 25.11
    # rustPlatform via the overlay's callPackage, its own inputs are only
    # used for its CI cross-builds.
    emacs-tramp-rpc.url = "github:ArthurHeymans/emacs-tramp-rpc";
  };

  outputs = { self, nixpkgs, omp, herdr, emacs-overlay, emacs-31, emacs-tramp-rpc }:
    let
      system = "x86_64-linux";
      mkVM = host: nixpkgs.lib.nixosSystem {
        inherit system;
        specialArgs = {
          ompPkg = omp.packages.${system}.default;
          herdrPkg = herdr.packages.${system}.default;
        };
        modules = [
          # Guests deliberately run the stock nixpkgs kernel so their system closure
          # substitutes from cache.nixos.org instead of rebuilding a custom kernel.
          { nixpkgs.overlays = [
              emacs-overlay.overlays.default
              (import ./overlays/emacs-31-branch.nix emacs-31)
              emacs-tramp-rpc.overlays.default
            ]; }
          ./hosts/common
          (./hosts + "/${host}")
        ];
      };
    in {
      nixosConfigurations = {
        devpro = mkVM "devpro";
        devhobby = mkVM "devhobby";
      };

      # Exposed for hash-bump iteration: `nix build .#openspec`.
      packages.${system} = {
        openspec =
          nixpkgs.legacyPackages.${system}.callPackage ./pkgs/openspec { };
      };
    };
}
