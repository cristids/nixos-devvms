{ pkgs, ... }:

# General development stack: devenv + direnv for per-project environments, gh for
# GitHub (auth is per-user — the token lands in the persistent /home when the user
# runs `gh auth login`; hobby-scoped token in devhobby, work-scoped in devpro).
{
  environment.systemPackages = with pkgs; [
    devenv
    gh
    python3
    uv
    unzip
    tree
    lazygit
    file
  ];

  programs.direnv = {
    enable = true;
    nix-direnv.enable = true;
  };

  # devenv.cachix.org is in hosts/common nix.settings substituters so `devenv up`
  # pulls its toolchains prebuilt.
}
