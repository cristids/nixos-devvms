{ lib, stdenv, fetchurl, autoPatchelfHook, makeWrapper, zlib, openssl }:
let
  binary = { pname, version, url, hash, executable }: stdenv.mkDerivation {
    inherit pname version;
    src = fetchurl { inherit url hash; };
    sourceRoot = ".";
    nativeBuildInputs = [ autoPatchelfHook makeWrapper ];
    buildInputs = [ stdenv.cc.cc.lib zlib openssl ];
    dontConfigure = true;
    dontBuild = true;
    dontStrip = true; # Bun standalone embeds its application beyond ordinary ELF sections.
    installPhase = ''
      runHook preInstall
      install -Dm755 $(find . -type f -name ${lib.escapeShellArg executable} | head -n1) $out/bin/${pname}
      runHook postInstall
    '';
    meta.mainProgram = pname;
  };
in {
  opencode = binary {
    pname = "opencode"; version = "1.19.6"; executable = "opencode";
    url = "https://github.com/anomalyco/opencode/releases/download/v1.19.6/opencode-linux-x64-baseline.tar.gz";
    hash = "sha256-hEMROz89/r+LD6kcQ3flUZpiuSMXMtk0Sl3wAeSoUrM=";
  };
  goose = binary {
    pname = "goose"; version = "1.60.1"; executable = "goose";
    url = "https://github.com/aaif-goose/goose/releases/download/v1.60.1/goose-x86_64-unknown-linux-gnu.tar.gz";
    hash = "sha256-AyexhX+AMDQ/Wbd1A+BFVim79HLCkBy5S4g4nWJ4Y1o=";
  };
}
