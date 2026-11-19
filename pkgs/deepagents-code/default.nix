{ lib, stdenv, python314, cacert, autoPatchelfHook, makeWrapper, zlib }:
let
  python3 = python314;
  python3Packages = python314.pkgs;
  version = "0.2.11"; # Stable upstream CLI release, verified 2026-11-19.
  wheels = stdenv.mkDerivation {
    pname = "deepagents-code-wheels";
    inherit version;
    dontUnpack = true;
    nativeBuildInputs = [ python3 python3Packages.pip cacert ];
    buildPhase = ''
      mkdir -p $out
      export SOURCE_DATE_EPOCH=315532800
      python -m pip wheel --no-cache-dir --disable-pip-version-check \
        --prefer-binary --constraint ${./constraints.txt} --wheel-dir $out 'deepagents-code==${version}'
    '';
    dontInstall = true;
    dontFixup = true;
    outputHashMode = "recursive";
    outputHashAlgo = "sha256";
    outputHash = "sha256-jotBJ12LGpjTcGQm1cvNpNllA8Hafqq16bjF1R/Iqto=";
  };
in stdenv.mkDerivation {
  pname = "deepagents-code";
  inherit version;
  dontUnpack = true;
  nativeBuildInputs = [ python3 python3Packages.pip autoPatchelfHook makeWrapper ];
  buildInputs = [ stdenv.cc.cc.lib zlib ];
  installPhase = ''
    mkdir -p $out/lib/python $out/bin
    python -m pip install --no-cache-dir --disable-pip-version-check \
      --no-index --no-deps --no-compile --target $out/lib/python ${wheels}/*.whl
    for name in dcode deepagents-code; do
      entry=$out/lib/python/bin/$name
      makeWrapper ${python3}/bin/python $out/bin/$name \
        --add-flags "$entry" --prefix PYTHONPATH : $out/lib/python \
        --set DO_NOT_TRACK 1 --set LANGSMITH_TRACING false
    done
  '';
  passthru = { inherit wheels; };
  meta = {
    description = "Deep Agents Code stable CLI and pinned wheel closure for isolated assessment";
    homepage = "https://github.com/langchain-ai/deepagents";
    platforms = [ "x86_64-linux" ];
    mainProgram = "dcode";
  };
}
