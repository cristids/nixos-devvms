{ lib, stdenv, fetchurl, nodejs, makeWrapper, cacert }:

# Same FOD pattern as nixos-laptops pkgs/pi-coding-agent, but tracking the current
# upstream org (@earendil-works — pi moved there from @mariozechner) at a version
# recent enough for oh-my-pi's peer range (>=0.74.0).
#
# Version bump procedure: change `version`, then fix TWO hashes — `src.hash`
# (nix-prefetch-url the tarball) and `deps.outputHash` (build once with a wrong
# hash, copy the "got:" value).
let
  version = "0.84.2";
  src = fetchurl {
    url = "https://registry.npmjs.org/@earendil-works/pi-coding-agent/-/pi-coding-agent-${version}.tgz";
    hash = "sha256-lbiZzXsaDB8BdMe/M6tCdDXjVTp9H0dWZhqpx/Gmj/o=";
  };

  # Fixed-output derivation that runs `npm install` to fetch the dep tree.
  deps = stdenv.mkDerivation {
    pname = "pi-coding-agent-deps";
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
    outputHash = "sha256-r0nx5q/QaTI/qeZKFJcR68ETyNvbG3JvGw4LByDU+JM=";
  };
in
stdenv.mkDerivation {
  pname = "pi-coding-agent";
  inherit version;

  dontUnpack = true;
  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall
    mkdir -p $out/bin $out/lib
    cp -r ${deps}/lib/node_modules $out/lib/
    # npm on PATH: `pi install npm:...` (extension manager) shells out to it.
    makeWrapper ${nodejs}/bin/node $out/bin/pi \
      --add-flags $out/lib/node_modules/@earendil-works/pi-coding-agent/dist/cli.js \
      --prefix PATH : ${nodejs}/bin
    runHook postInstall
  '';

  meta = with lib; {
    description = "Coding agent CLI with read, bash, edit, write tools and session management";
    homepage = "https://github.com/badlogic/pi-mono";
    license = licenses.mit;
    platforms = platforms.linux;
    mainProgram = "pi";
  };
}
