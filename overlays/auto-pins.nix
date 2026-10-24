# Packages held back by devvm-update-auto (runs on cdssrv02, defined in
# nixos_llm_srv/modules/system/devvm-update/devvm_update.py). Do not edit
# ../auto-pins.json by hand unless you mean it; the weekly run rewrites it.
#
# When an input bump breaks the build of a single leaf package
# (the laptops hit this 2026-10-24 with khal), the run pins just that package to the last
# nixpkgs rev where it built and still rolls the rest of the update. Each
# run first retries with no pins, so a pin disappears as soon as the package
# builds on current nixpkgs again.
#
# auto-pins.json: { "<attr>": { "rev", "narHash", "since", "name" }, ... }
final: prev:
let
  pins = builtins.fromJSON (builtins.readFile ../auto-pins.json);

  # One nixpkgs instance per distinct rev, not per package.
  revs = builtins.listToAttrs (
    map (p: {
      name = p.rev;
      value = import (builtins.fetchTree {
        type = "github";
        owner = "NixOS";
        repo = "nixpkgs";
        inherit (p) rev narHash;
      }) {
        inherit (prev.stdenv.hostPlatform) system;
        inherit (prev) config;
      };
    }) (builtins.attrValues pins)
  );
in
builtins.mapAttrs (attr: p: revs.${p.rev}.${attr}) pins
