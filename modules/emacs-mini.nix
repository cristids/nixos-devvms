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

      # 3. Personal repos (private) — need gh auth; quietly skip until then.
      if ! gh auth token >/dev/null 2>&1; then
        log "gh not authenticated — framework only; run 'gh auth login', then mini-sync"
        exit 0
      fi
      gh auth setup-git >/dev/null 2>&1 || true
      # post-init.el installs natural-mode/studio/socrates via package-vc from
      # git@github.com: URLs. The VMs carry no GitHub SSH key on purpose
      # (laptops ssh in, nothing sshes out), so rewrite ssh→https and let gh's
      # credential helper answer instead.
      git config --global url."https://github.com/".insteadOf "git@github.com:"

      # Clone/refresh a private cristids repo into ~/cristids/<name>.
      sync_repo() {
        dir="$HOME/cristids/$1"
        if [ ! -d "$dir/.git" ]; then
          log "cloning cristids/$1 → $dir"
          mkdir -p "$HOME/cristids"
          gh repo clone "cristids/$1" "$dir" -- --depth 50 \
            || { log "clone of $1 failed; skipping"; return 1; }
        fi
        if ! git -C "$dir" diff --quiet || ! git -C "$dir" diff --cached --quiet; then
          log "local changes present in $dir; skipping pull"
        else
          git -C "$dir" pull --ff-only --quiet 2>/dev/null || true
        fi
      }

      # natural-mode: post-init.el only defines the menu groups (AI, Notes —
      # the tap-Alt / M-n surface) when ~/cristids/natural-mode exists.
      sync_repo natural-mode || true
      sync_repo mini || exit 0

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
