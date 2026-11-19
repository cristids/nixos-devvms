{ stdenv, fetchurl, autoPatchelfHook, makeWrapper, openssl, zlib }:
stdenv.mkDerivation {
  pname = "kimi-code";
  version = "2.6.3";
  src = fetchurl {
    url = "https://github.com/MoonshotAI/kimi-code/releases/download/%40moonshot-ai/kimi-code%402.6.3/kimi-code-linux-x64.tar.gz";
    sha256 = "a138b9854236c2b97f8bd6f28f93dd756f720383177e5cf0f95810b2872bab43";
  };
  dontUnpack = true;
  # Keep the embedded JavaScript payload in the standalone executable intact.
  dontStrip = true;
  nativeBuildInputs = [ autoPatchelfHook makeWrapper ];
  buildInputs = [ stdenv.cc.cc.lib openssl zlib ];
  installPhase = ''
    mkdir -p $out/lib/kimi-code $out/bin
    tar -xzf $src -C $out/lib/kimi-code
    makeWrapper $out/lib/kimi-code/kimi $out/bin/kimi --set DO_NOT_TRACK 1
  '';
  meta = {
    description = "Kimi Code CLI stable release for isolated harness assessment";
    homepage = "https://github.com/MoonshotAI/kimi-code";
    platforms = [ "x86_64-linux" ];
    mainProgram = "kimi";
  };
}
