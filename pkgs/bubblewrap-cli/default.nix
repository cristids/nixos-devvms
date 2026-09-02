{
  lib,
  stdenv,
  fetchurl,
  nodejs,
  makeWrapper,
  cacert,
}:

let
  version = "1.25.0";
  src = fetchurl {
    url = "https://registry.npmjs.org/@bubblewrap/cli/-/cli-${version}.tgz";
    hash = "sha256-aFifusCyUo1zhBFfDu0WVdAMXZIq4oVQCqxXy7DUR0A=";
  };

  # Materialize the npm dependency tree as a fixed-output derivation. This
  # keeps the final package reproducible while packaging the upstream npm
  # release directly.
  deps = stdenv.mkDerivation {
    pname = "bubblewrap-cli-deps";
    inherit version src;

    nativeBuildInputs = [
      nodejs
      cacert
    ];

    buildPhase = ''
      runHook preBuild
      export HOME=$PWD/.home
      mkdir -p $HOME
      npm install --omit=dev --ignore-scripts --no-audit --no-fund \
        --prefix $out --global ${src}
      runHook postBuild
    '';

    dontInstall = true;
    dontFixup = true;

    outputHashMode = "recursive";
    outputHashAlgo = "sha256";
    outputHash = "sha256-+jHp+z1cCqucOZVbH4cvRp9YyBxFkLv0teZDYVnZIkU=";
  };
in
stdenv.mkDerivation {
  pname = "bubblewrap-cli";
  inherit version;

  dontUnpack = true;
  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall
    mkdir -p $out/bin $out/lib
    cp -r ${deps}/lib/node_modules $out/lib/
    makeWrapper ${nodejs}/bin/node $out/bin/bubblewrap \
      --add-flags $out/lib/node_modules/@bubblewrap/cli/bin/bubblewrap.js
    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    test -x $out/bin/bubblewrap
    ${nodejs}/bin/node --check \
      $out/lib/node_modules/@bubblewrap/cli/bin/bubblewrap.js
    ${nodejs}/bin/node -e \
      'assert.equal(require(process.argv[1]).version, "${version}")' \
      $out/lib/node_modules/@bubblewrap/cli/package.json
  '';

  meta = {
    description = "CLI for generating Android Trusted Web Activity projects from PWAs";
    homepage = "https://github.com/GoogleChromeLabs/bubblewrap";
    license = lib.licenses.asl20;
    mainProgram = "bubblewrap";
    platforms = lib.platforms.linux;
  };
}
