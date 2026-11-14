{ lib, callPackage }:

# Runs on the official nodejs.org binary (../nodejs-official): dsh's native addon
# node-addon-require-builtin refuses every nixpkgs node build (machine-code probe).

# dsh — DeepSeek Harness CLI (@deepseek-ai/dsh), pinned exact (bake-off 2026-11-05,
# devhobby, harness-pair arm). Run e.g. `dsh --profile headless "job"`.
let version = "0.2.1-rc.5"; in
(callPackage ../npm-global { nodejs = callPackage ../nodejs-official { }; }) {
  pname = "dsh";
  inherit version;
  specs = [ "@deepseek-ai/dsh@${version}" ];
  depsHash = "sha256-o6AaDifs7TysR/ozAVJxfesVsxqOzGFkMbJus+ylLng=";
  resolveBefore = "2026-11-14";  # moves with depsHash only when this package is bumped
  bins.dsh = "@deepseek-ai/dsh/lib/bin.js";
  # Any non-empty value hard-disables the session-telemetry-otel row (the only
  # telemetry in the headless/base profiles; the web app's product telemetry is not
  # used here). Forced, not a default.
  wrapArgs = [ "--set" "DSH_TELEMETRY_DISABLED" "1" ];
  meta = {
    description = "DeepSeek Harness CLI";
    homepage = "https://www.npmjs.com/package/@deepseek-ai/dsh";
    mainProgram = "dsh";
  };
}
