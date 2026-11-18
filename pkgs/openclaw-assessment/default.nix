{ lib, callPackage, nodejs_24 }:
# Stable GitHub release verified 2026-11-18; isolated assessment only.
((callPackage ../npm-global { nodejs = nodejs_24; }) {
  pname = "openclaw-assessment";
  version = "2026.11.5";
  specs = [ "openclaw@2026.11.5" ];
  depsHash = "sha256-/5r6vhPVGThBSxHVYo2oJ78R8JdmO4s+oCVHdWOEthI=";
  resolveBefore = "2026-11-18T19:00:00Z";
  bins.openclaw = "openclaw/openclaw.mjs";
  wrapArgs = [ "--set" "DO_NOT_TRACK" "1" ];
  meta = {
    description = "OpenClaw stable CLI for isolated harness assessment";
    homepage = "https://github.com/openclaw/openclaw";
    mainProgram = "openclaw";
  };
}) .overrideAttrs (old: {
  postInstall = (old.postInstall or "") + ''
    # Complete upstream lifecycle while the package is still writable.
    chmod -R u+w $out/lib/node_modules/openclaw
    ${nodejs_24}/bin/node $out/lib/node_modules/openclaw/scripts/preinstall-package-manager-warning.mjs
    ${nodejs_24}/bin/node $out/lib/node_modules/openclaw/scripts/postinstall-bundled-plugins.mjs
    $out/bin/openclaw --version
  '';
})
