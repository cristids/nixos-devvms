{ pkgs, ... }:

# Terminal emacs for the VMs — since 2026-08-23 the same emacs-31 release branch
# the laptops run (flake inputs emacs-overlay + emacs-31, overlays/emacs-31-branch.nix),
# just the -nox build: these are headless jails, sessions happen in tmux over
# ssh/mosh. Version parity matters because the SAME personal config (cristids/mini
# post-init.el, validated on the laptops' 31) loads here. Costs the
# cache.nixos.org substitute emacs30-nox had — cdssrv02 builds it, Harmonia serves it.
#
# tree-sitter grammars are baked in the same way the laptops do it (emacsPackagesFor
# + treesit-grammars.with-all-grammars → wrapper puts them on treesit-extra-load-path)
# so *-ts-mode works without per-machine grammar compiles.
#
# Personal distro configs (cristids/craft etc.) are PRIVATE repos — the user clones
# them into the persistent /home after `gh auth login`; nothing here can or should.
let
  emacsWithGrammars =
    (pkgs.emacsPackagesFor pkgs.emacs-unstable-nox).withPackages (epkgs: [
      epkgs.treesit-grammars.with-all-grammars
    ]);
in
{
  # Same escape hatch as the laptops: lets eglot/dap run mason-style prebuilt
  # dynamically-linked binaries a project might download.
  programs.nix-ld.enable = true;

  environment.systemPackages = with pkgs; [
    emacsWithGrammars

    # Support tooling emacs shells out to (subset of nixos-laptops
    # modules/core/emacs.nix, minus GUI-only bits like pandoc/pdf toolchain).
    fd
    ripgrep
    gnugrep
    shellcheck
    cmake
    nodejs                      # npm + npx (MCP servers etc.)
    clang-tools                 # clangd — C/C++ LSP
    typescript-language-server  # JS/TS LSP
    gcc
    gnumake
    libtool
    pkg-config
    sqlite
    ispell
    gdb
    lldb
    ruff                        # Python LSP+linter for eglot
    copilot-language-server     # copilot.el finds it on PATH (same as laptops);
                                # without it every prog-mode buffer errors
                                # "@github/copilot-language-server is not installed".
                                # Auth is per-machine here: M-x copilot-login once
                                # (laptops sync ~/.config/github-copilot, VMs don't).
  ];
}
