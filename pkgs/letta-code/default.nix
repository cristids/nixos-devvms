{ lib, callPackage, nodejs_24 }:
# Version selected from the upstream stable GitHub release, not npm latest.
(callPackage ../npm-global { nodejs = nodejs_24; }) {
  pname = "letta-code";
  version = "0.34.65";
  specs = [ "@letta-ai/letta-code@0.34.65" ];
  depsHash = "sha256-Ja3gjvUgXGbJKYPaNOXCtmpCJbi/9svMfxU+n8tfaYs=";
  resolveBefore = "2026-11-18T17:00:00Z";
  bins.letta = "@letta-ai/letta-code/letta.js";
  wrapArgs = [ "--set" "DO_NOT_TRACK" "1" "--set" "SCARF_ANALYTICS" "false" ];
  meta = {
    description = "Letta Code terminal agent";
    homepage = "https://github.com/letta-ai/letta-code";
    license = lib.licenses.asl20;
    mainProgram = "letta";
  };
}
