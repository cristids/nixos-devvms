{ lib, callPackage, nodejs_26 }:
# Latest stable CLI tag v0.28.0 verified upstream on 2026-11-18.
(callPackage ../npm-global { nodejs = nodejs_26; }) {
  pname = "qwen-code";
  version = "0.28.0";
  specs = [ "@qwen-code/qwen-code@0.28.0" ];
  depsHash = "sha256-8ewXekjO/pie2R19HRbmmIgEXF3+qJn0l3pILb1kD/E=";
  resolveBefore = "2026-11-18T20:00:00Z";
  bins.qwen = "@qwen-code/qwen-code/cli-entry.js";
  wrapArgs = [ "--set" "DO_NOT_TRACK" "1" ];
  meta = {
    description = "Qwen Code stable CLI for isolated harness assessment";
    homepage = "https://github.com/QwenLM/qwen-code";
    mainProgram = "qwen";
  };
}
