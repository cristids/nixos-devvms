{ lib, stdenv, fetchurl, autoPatchelfHook }:

# The official nodejs.org Linux x64 build, ELF-patched for NixOS (no other change).
# Needed only by dsh (2026-11-05): its native addon node-addon-require-builtin locates a
# Node internal by pattern-matching the machine code of the OFFICIAL binaries and refuses
# every nixpkgs node build (22/24/26: "Unsupported/no-getter"). Everything else keeps
# using nixpkgs' nodejs. Bump = version + hash (nix store prefetch-file --json <url>).
stdenv.mkDerivation rec {
  pname = "nodejs-official-bin";
  version = "24.22.1";
  src = fetchurl {
    url = "https://nodejs.org/dist/v${version}/node-v${version}-linux-x64.tar.xz";
    hash = "sha256-bBcqmrZxj3uAH7WnvUhq8u5eMnWAhqBLhPzOKqdwleY=";
  };
  nativeBuildInputs = [ autoPatchelfHook ];
  buildInputs = [ stdenv.cc.cc.lib ];
  dontConfigure = true;
  dontBuild = true;
  dontStrip = true;   # stripping would change the code the addon probes
  installPhase = ''
    mkdir -p $out
    cp -r bin include lib share $out/
  '';
  meta = {
    description = "Node.js ${version} (official binary build)";
    homepage = "https://nodejs.org";
    license = lib.licenses.mit;
    platforms = [ "x86_64-linux" ];
    mainProgram = "node";
  };
}
