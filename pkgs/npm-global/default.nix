{ lib, stdenv, nodejs, makeWrapper, cacert }:

# Generic "npm install --global" packaging for npm-only CLIs (pi, taskplane, dsh),
# the same fixed-output pattern pi used before 2026-08-23 (git show f10c751^).
#
#   specs     npm install specs, exact versions only (e.g. "taskplane@0.30.6")
#   depsHash  outputHash of the FOD (build once with lib.fakeHash, copy "got:")
#   bins      { <name> = "<path below lib/node_modules>"; } wrapped with node
#   wrapArgs  extra makeWrapper args for every bin (env defaults, PATH)
#
# NVM_BIN=$out/bin: tools that look for sibling global packages the nvm way
# ($NVM_BIN/../lib/node_modules) find this tree. taskplane's forked engine worker needs it to
# locate pi's dist/cli.js (its argv[1] is not pi's; smoke 11-05: "Cannot find Pi CLI").
#
# Bump = edit specs + set depsHash = lib.fakeHash, build, paste the "got:" hash.
{ pname, version, specs, depsHash, bins, wrapArgs ? [ ], npmFlags ? [ ], meta ? { } }:
let
  deps = stdenv.mkDerivation {
    pname = "${pname}-deps";
    inherit version;
    dontUnpack = true;
    nativeBuildInputs = [ nodejs cacert ];
    buildPhase = ''
      runHook preBuild
      export HOME=$PWD/.home
      mkdir -p $HOME
      npm install --global --prefix $out --omit=dev --ignore-scripts \
        --no-audit --no-fund --no-update-notifier ${lib.escapeShellArgs npmFlags} \
        ${lib.escapeShellArgs specs}
      # npm writes absolute bin symlinks and cache metadata that differ between
      # runs; only lib/node_modules is used.
      rm -rf $out/bin $out/etc
      find $out -name .package-lock.json -delete
      runHook postBuild
    '';
    dontInstall = true;
    # FODs must not reference store paths: no shebang patching here.
    dontFixup = true;
    outputHashMode = "recursive";
    outputHashAlgo = "sha256";
    outputHash = depsHash;
  };
in
stdenv.mkDerivation {
  inherit pname version;
  dontUnpack = true;
  nativeBuildInputs = [ makeWrapper ];
  installPhase = ''
    runHook preInstall
    mkdir -p $out/bin $out/lib
    cp -r ${deps}/lib/node_modules $out/lib/
    ${lib.concatStrings (lib.mapAttrsToList (name: path: ''
      makeWrapper ${nodejs}/bin/node $out/bin/${name} \
        --add-flags $out/lib/node_modules/${path} \
        --prefix PATH : ${nodejs}/bin --set-default NVM_BIN $out/bin ${lib.escapeShellArgs wrapArgs}
    '') bins)}
    runHook postInstall
  '';
  passthru = { inherit deps; };
  meta = { platforms = lib.platforms.linux; } // meta;
}
