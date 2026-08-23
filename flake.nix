{
  description = "Dev VM guests (devpro/devhobby) — microvm.nix jails hosted on cdssrv03";

  # Same layout idea as nixos-laptops: hosts/ + modules/ + pkgs/, one flake, split by
  # machine. These configs are CONSUMED by cdssrv03's flake (input `devvms`) — the host
  # builds the guest closures and shares them into the VMs via the read-only virtiofs
  # /nix/store. Deploying = rebuild on the host + restart microvm@<name>; nothing is
  # ever deployed from inside a guest.
  inputs = {
    # Same nixpkgs line as cdssrv03 (the consumer overrides this with `follows` so the
    # guests share the host's store paths instead of pulling a second nixpkgs).
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";

    microvm = {
      url = "github:microvm-nix/microvm.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # omp (can1357/oh-my-pi) — THE oh-my-pi, Can Bölük's fork of pi. Deliberately NO
    # nixpkgs follows: its build (rust-overlay + bun2nix) is pinned to its own inputs
    # and forcing our 25.11 under it would just break the from-source build.
    omp.url = "github:can1357/oh-my-pi";

    # Emacs 31 for the VMs, same two-piece recipe as nixos-laptops: emacs-overlay
    # gives `emacs-unstable-nox', and the emacs-31 release branch is swapped in as
    # its src (overlays/emacs-31-branch.nix) because emacs-overlay's unstable
    # tracks pretest tags only and skipped 31.1-rc1. Costs the cache.nixos.org
    # substitute the old emacs30-nox had: built once on cdssrv02, cdssrv03 pulls
    # it from Harmonia. Drop both inputs once nixpkgs stable carries emacs31.
    emacs-overlay = {
      url = "github:nix-community/emacs-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    emacs-31 = {
      url = "github:emacs-mirror/emacs/emacs-31";
      flake = false;
    };
  };

  outputs = { self, nixpkgs, microvm, omp, emacs-overlay, emacs-31 }:
    let
      system = "x86_64-linux";
      mkVM = host: nixpkgs.lib.nixosSystem {
        inherit system;
        specialArgs = { ompPkg = omp.packages.${system}.default; };
        modules = [
          # Guests deliberately run the stock nixpkgs kernel (no cachyos overlay) so
          # their whole closure substitutes from cache.nixos.org instead of rebuilding.
          microvm.nixosModules.microvm
          { nixpkgs.overlays = [
              emacs-overlay.overlays.default
              (import ./overlays/emacs-31-branch.nix emacs-31)
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
