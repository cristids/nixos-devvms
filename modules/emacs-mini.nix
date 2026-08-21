{ pkgs, ... }:

# The "mini" emacs distro — same model as nixos-laptops modules/home/emacs.nix but
# slimmed to the one distro the VMs carry:
#
#   ~/.local/share/emacs-distros/mini  ← jamescherti/minimal-emacs.d (public, auto-cloned)
#   ~/cristids/mini                    ← personal overlay (PRIVATE repo — needs `gh auth
#                                        login` once; post-init.el etc. get symlinked
#                                        into the framework dir, which gitignores them)
#   ~/.config/emacs → the mini dir     (plain symlink; /home is persistent)
#
# `mini-sync` is idempotent and runs at every boot via the oneshot below: the
# framework appears on first boot with network; the personal overlay appears
# automatically on the first boot after gh is authenticated (or run `mini-sync`
# by hand). No emacs daemon in the VMs — sessions are `emacs -nw` in tmux.
let
  miniSync = pkgs.writeShellApplication {
    name = "mini-sync";
    runtimeInputs = with pkgs; [ git gh coreutils findutils ];
    text = ''
      set -u
      DISTROS_DIR="$HOME/.local/share/emacs-distros"
      FRAMEWORK_DIR="$DISTROS_DIR/mini"
      REPO_DIR="$HOME/cristids/mini"
      REPO_SLUG="cristids/mini"

      log() { printf '[mini-sync] %s\n' "$*"; }

      # 1. Framework (public) — clone if missing.
      if [ ! -d "$FRAMEWORK_DIR/.git" ]; then
        log "cloning minimal-emacs.d → $FRAMEWORK_DIR"
        mkdir -p "$DISTROS_DIR"
        git clone --depth 1 https://github.com/jamescherti/minimal-emacs.d \
          "$FRAMEWORK_DIR" || { log "framework clone failed (no network?)"; exit 0; }
      fi

      # 2. ~/.config/emacs → framework. Back up a real dir, replace symlinks.
      cfg="$HOME/.config/emacs"
      mkdir -p "$HOME/.config"
      if [ -e "$cfg" ] && [ ! -L "$cfg" ]; then
        mv "$cfg" "$cfg.$(date +%Y%m%d-%H%M%S).bak"
        log "backed up real $cfg"
      fi
      ln -sfn "$FRAMEWORK_DIR" "$cfg"
      # A stray ~/.emacs.d would shadow ~/.config/emacs.
      if [ -e "$HOME/.emacs.d" ] && [ ! -L "$HOME/.emacs.d" ]; then
        mv "$HOME/.emacs.d" "$HOME/.emacs.d.$(date +%Y%m%d-%H%M%S).bak"
        log "backed up stray ~/.emacs.d"
      fi

      # 3. Personal overlay (private) — needs gh auth; quietly skip until then.
      if ! gh auth token >/dev/null 2>&1; then
        log "gh not authenticated — framework only; run 'gh auth login', then mini-sync"
        exit 0
      fi
      gh auth setup-git >/dev/null 2>&1 || true

      if [ ! -d "$REPO_DIR/.git" ]; then
        log "cloning $REPO_SLUG → $REPO_DIR"
        mkdir -p "$(dirname "$REPO_DIR")"
        gh repo clone "$REPO_SLUG" "$REPO_DIR" -- --depth 50 \
          || { log "gh clone failed; skipping"; exit 0; }
      fi

      cd "$REPO_DIR"
      if ! git diff --quiet || ! git diff --cached --quiet; then
        log "local changes present in $REPO_DIR; skipping pull"
      else
        git pull --ff-only --quiet 2>/dev/null || true
      fi

      # 4. Symlink personal files over the framework (it gitignores post-init.el etc.)
      for src in "$REPO_DIR"/* "$REPO_DIR"/.[!.]*; do
        [ -e "$src" ] || continue
        name="$(basename "$src")"
        case "$name" in
          .git|.gitignore|.gitattributes|README*|LICENSE*) continue ;;
        esac
        link="$FRAMEWORK_DIR/$name"
        if [ -L "$link" ] && [ "$(readlink "$link")" = "$src" ]; then
          continue
        fi
        if [ -e "$link" ] && [ ! -L "$link" ]; then
          mv "$link" "$link.$(date +%Y%m%d-%H%M%S).bak"
          log "backed up real file $link"
        fi
        ln -sfn "$src" "$link"
        log "linked $name"
      done
      log "done"
    '';
  };
in
{
  environment.systemPackages = [ miniSync ];

  systemd.services.mini-emacs-setup = {
    description = "Clone/sync the mini emacs distro for cristian";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      User = "cristian";
      Group = "users";
      Environment = "HOME=/home/cristian";
    };
    script = "${miniSync}/bin/mini-sync";
  };
}
