{ lib, callPackage }:

# pi 1.5.0 (@earendil-works/pi-coding-agent) + taskplane, the pi orchestration
# extension, in ONE node_modules tree (bake-off 2026-11-05, devhobby). taskplane is
# loaded as a local pi package from $out/lib/node_modules/taskplane (no runtime
# `pi install npm:…`); it imports no pi module itself (pi maps its host packages for
# extensions), so its "*" peer deps are not installed (--legacy-peer-deps).
#
# taskplane resolves the pi CLI from process.argv[1] (must end in cli.js), which the
# wrapper satisfies. Telemetry/update checks off by default (PI_TELEMETRY=0,
# PI_SKIP_VERSION_CHECK=1); the settings file also sets enableInstallTelemetry=false.
let
  piVersion = "1.5.0";
  taskplaneVersion = "0.30.6";
in
(callPackage ../npm-global { }) {
  pname = "pi-coding-agent";
  version = piVersion;
  specs = [
    "@earendil-works/pi-coding-agent@${piVersion}"
    "taskplane@${taskplaneVersion}"
  ];
  npmFlags = [ "--legacy-peer-deps" ];
  depsHash = "sha256-nTiFaWQvX8jGFEliIareOkywpqYO99lcIFK/mzUZDB0=";
  bins = {
    pi = "@earendil-works/pi-coding-agent/dist/bundle/cli.js";
    taskplane = "taskplane/bin/taskplane.mjs";
  };
  wrapArgs = [
    "--set" "PI_TELEMETRY" "0"
    "--set-default" "PI_SKIP_VERSION_CHECK" "1"
  ];
  meta = {
    description = "pi coding agent ${piVersion} + taskplane ${taskplaneVersion}";
    homepage = "https://github.com/earendil-works/pi";
    license = lib.licenses.mit;
    mainProgram = "pi";
  };
}
