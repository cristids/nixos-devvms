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
  };

  outputs = { self, nixpkgs, microvm }:
    let
      system = "x86_64-linux";
      mkVM = host: nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          # Guests deliberately run the stock nixpkgs kernel (no cachyos overlay) so
          # their whole closure substitutes from cache.nixos.org instead of rebuilding.
          microvm.nixosModules.microvm
          ./hosts/common
          (./hosts + "/${host}")
        ];
      };
    in {
      nixosConfigurations = {
        devpro = mkVM "devpro";
        devhobby = mkVM "devhobby";
      };

      # Exposed for hash-bump iteration: `nix build .#pi-coding-agent` etc.
      # (oh-my-pi is NOT nix-packaged: it is a pi extension installed per-user by
      # pi's own package manager — see modules/agents.nix.)
      packages.${system} = {
        pi-coding-agent =
          nixpkgs.legacyPackages.${system}.callPackage ./pkgs/pi-coding-agent { };
        openspec =
          nixpkgs.legacyPackages.${system}.callPackage ./pkgs/openspec { };
      };
    };
}
