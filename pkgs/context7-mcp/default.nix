# Context7 MCP server (@upstash/context7-mcp), newer than nixpkgs stable (2.2.5).
# Reuses the nixpkgs recipe (pnpm workspace build + `pnpm deploy`) and only swaps
# the pinned tag and the two hashes. Nix-only rule: no `npx -y` on the VMs.
#
# Bump: change `version`, set both hashes to "", then
#   nix build path:.#context7-mcp   (twice: src hash, then pnpmDeps hash)
{ context7-mcp, fetchFromGitHub, fetchPnpmDeps, pnpm_10_latest }:

context7-mcp.overrideAttrs (finalAttrs: prev: {
  version = "4.5.0";

  src = fetchFromGitHub {
    owner = "upstash";
    repo = "context7";
    tag = "@upstash/context7-mcp@${finalAttrs.version}";
    hash = "sha256-TLRU0Tqe3Rnwp9T2fTvKbSk/nnBvy+vk5311lhOS76o=";
  };

  pnpmDeps = fetchPnpmDeps {
    inherit (finalAttrs) pname version src;
    pnpm = pnpm_10_latest;
    fetcherVersion = 3;
    hash = "sha256-p4jQo75iwF5r9FydVE9dfUyaaRpHRpH9Ctz3GA8bX+w=";
  };
})
