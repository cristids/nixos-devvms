{ lib, stdenv, fetchurl, autoPatchelfHook, makeWrapper, openssl, zlib }:
stdenv.mkDerivation {
  pname = "prime-agent";
  version = "0.10.5";
  src = fetchurl {
    url = "https://github.com/PrimeIntellect-ai/prime-agent/releases/download/v0.10.5/prime-agent-0.10.5-linux-x64.tar.gz";
    sha256 = "b9f653f4c32e83a2270f0b52edea6a72c7bdbd1f7b5c3e64c55e75157094f3b3";
  };
  dontUnpack = true;
  nativeBuildInputs = [ autoPatchelfHook makeWrapper ];
  buildInputs = [ stdenv.cc.cc.lib openssl zlib ];
  installPhase = ''
    mkdir -p $out/lib/prime-agent $out/bin
    tar -xzf $src -C $out/lib/prime-agent
    makeWrapper $out/lib/prime-agent/prime-agent $out/bin/prime-agent \
      --set DO_NOT_TRACK 1
  '';
  meta = {
    description = "Prime Agent stable release for isolated assessment preparation";
    homepage = "https://github.com/PrimeIntellect-ai/prime-agent";
    platforms = [ "x86_64-linux" ];
    mainProgram = "prime-agent";
  };
}
