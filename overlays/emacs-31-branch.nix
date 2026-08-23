# Follow the emacs-31 release branch — the -nox twin of nixos-laptops
# overlays/emacs-31-branch.nix (see that file for the full why: emacs-overlay's
# `emacs-unstable' sits on the pretest tags and skipped 31.1-rc1, so the branch
# source comes in via the `emacs-31' flake input and is swapped underneath the
# overlay build here). `nix flake update emacs-31` + push advances the VMs to
# branch head. Remove this overlay and the input once emacs-unstable reaches
# 31.1 final (or nixpkgs stable grows emacs31-nox).
src:
final: prev: {
  emacs-unstable-nox = prev.emacs-unstable-nox.overrideAttrs (old: {
    inherit src;
    version = "31.1-${src.shortRev or "git"}";
    name = "emacs-nox-31.1-${src.shortRev or "git"}";
  });
}
