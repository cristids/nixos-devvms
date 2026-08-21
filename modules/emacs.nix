{ pkgs, ... }:

# Terminal emacs for the VMs. Deliberately NOT the laptops' pgtk/emacs-31 build —
# these are headless jails, sessions happen in tmux over ssh/mosh, so the stock
# nixpkgs emacs30-nox substitutes straight from cache.nixos.org.
#
# tree-sitter grammars are baked in the same way the laptops do it (emacsPackagesFor
# + treesit-grammars.with-all-grammars → wrapper puts them on treesit-extra-load-path)
# so *-ts-mode works without per-machine grammar compiles.
#
# Personal distro configs (cristids/craft etc.) are PRIVATE repos — the user clones
# them into the persistent /home after `gh auth login`; nothing here can or should.
let
  emacsWithGrammars =
    (pkgs.emacsPackagesFor pkgs.emacs30-nox).withPackages (epkgs: [
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
  ];
}
