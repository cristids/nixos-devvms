{ lib, stdenv, fetchurl, nodejs, makeWrapper, cacert }:

# OpenSpec (openspec.dev, @fission-ai/openspec) — spec-driven planning layer for
# coding agents. Same npm-FOD pattern as ./pi-coding-agent.
#
# Version bump procedure: change `version`, then fix TWO hashes — `src.hash`
# (nix-prefetch-url the tarball) and `deps.outputHash` (build once with a wrong
# hash, copy the "got:" value).
let
  version = "1.10.0";
  src = fetchurl {
    url = "https://registry.npmjs.org/@fission-ai/openspec/-/openspec-${version}.tgz";
    hash = "sha256-/vzxt9HjjPBqMnnGJFFw3LDSYdjWyOrT8wlrQfS5cfw=";
  };

  # Fixed-output derivation that runs `npm install` to fetch the dep tree.
  deps = stdenv.mkDerivation {
    pname = "openspec-deps";
    inherit version src;

    nativeBuildInputs = [ nodejs cacert ];

    buildPhase = ''
      runHook preBuild
      export HOME=$PWD/.home
      mkdir -p $HOME
      npm install --omit=dev --ignore-scripts --no-audit --no-fund \
        --prefix $out --global ${src}
      runHook postBuild
    '';

    dontInstall = true;
    # FODs must not reference store paths; skip fixup (shebang patching) entirely —
    # the real patching happens in the outer derivation.
    dontFixup = true;

    outputHashMode = "recursive";
    outputHashAlgo = "sha256";
    outputHash = "sha256-lpAzmIqNLi98eaWyBQJBUx/8M159I0cHJnrigzurfAI=";
  };
in
stdenv.mkDerivation {
  pname = "openspec";
  inherit version;

  dontUnpack = true;
  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall
    mkdir -p $out/bin $out/lib
    cp -r ${deps}/lib/node_modules $out/lib/
    makeWrapper ${nodejs}/bin/node $out/bin/openspec \
      --add-flags $out/lib/node_modules/@fission-ai/openspec/bin/openspec.js
    runHook postInstall
  '';

  meta = with lib; {
    description = "Spec-driven development framework for AI coding agents";
    homepage = "https://openspec.dev";
    license = licenses.mit;
    platforms = platforms.linux;
    mainProgram = "openspec";
  };
}
