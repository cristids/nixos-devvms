{ lib, stdenv, fetchurl, autoPatchelfHook, libsecret, glib, vulkan-loader }:

# T3 Code server + CLI (`t3`), upstream's prebuilt Linux release.
#
# Copy of nixos_llm_srv pkgs/t3code — bump all copies together (server here and
# on cdssrv02, desktop client in nixos-laptops modules/home/t3code.nix).
#
# Why not nixpkgs' t3code: nixpkgs-unstable is at 0.1.2, upstream at 0.1.9, and
# client and server must speak the same protocol — the store apps (iOS/Android)
# track the latest release and refuse an older server. Bump version + hash from
# the release's SHA256SUMS (t3-<v>-linux-x64.tar.gz).
#
# `t3` is a Node single-executable app: patch only the interpreter/rpath, never
# strip it (that would drop the embedded app blob).

stdenv.mkDerivation rec {
  pname = "t3code-bin";
  version = "0.1.9";

  src = fetchurl {
    url = "https://github.com/pingdotgg/t3code/releases/download/v${version}/t3-${version}-linux-x64.tar.gz";
    sha256 = "0141025335da334759cecd8db3483e09772f3600ca03674698524e7d94291362";
  };

  nativeBuildInputs = [ autoPatchelfHook ];
  buildInputs = [ stdenv.cc.cc.lib libsecret glib vulkan-loader ];

  dontStrip = true;
  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall
    # musl-only native modules never load on glibc; drop them so autoPatchelf
    # does not look for libc.musl.
    find node_modules -type d \( -name '*-musl' -o -name 'musl_*' \) -prune -exec rm -rf {} +
    mkdir -p $out/libexec/t3code $out/bin
    cp -r . $out/libexec/t3code/
    ln -s $out/libexec/t3code/t3 $out/bin/t3
    runHook postInstall
  '';

  meta = {
    description = "T3 Code: control plane for coding agents (server + t3 CLI)";
    homepage = "https://github.com/pingdotgg/t3code";
    license = lib.licenses.mit;
    mainProgram = "t3";
    platforms = [ "x86_64-linux" ];
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
  };
}
