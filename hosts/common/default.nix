{ config, lib, pkgs, ... }:

# Shared base for the dev VMs (host side lives in cdssrv02's flake:
# modules/system/dev-vm.nix — moved off cdssrv03 2026-08-27 when that box was
# earmarked for a bare-metal Ubuntu vast.ai reinstall; per-VM deltas:
# ../devpro, ../devhobby — hostname + MAC live there).
#
# Two VMs on purpose: `devpro` (professional work — will eventually hold SCOPED work
# credentials, e.g. an Azure DevOps key, so agents in it get driven with more care) and
# `devhobby` (hobby projects / agent playground). The VM boundary keeps each other's
# blast radius out.
#
# Credential posture (updated 2026-08-21): each VM carries a per-VM Melious API key
# (/var/lib/melious.key, see ../../modules/agents.nix) and whatever per-user tokens the
# user installs by hand (gh auth, private repo clones). Still NO fleet credentials, no
# laptop private keys, no deploy keys, no tailscale. Laptops ssh IN; nothing ssh's out.
# Coding agents (pi / oh-my-pi) run inside these VMs only — never on the host.
{
  imports = [
    ../../modules/emacs.nix
    ../../modules/emacs-mini.nix
    ../../modules/dev-tooling.nix
    ../../modules/agents.nix
  ];

  system.stateVersion = "25.11";
  time.timeZone = "Europe/Bucharest";
  nixpkgs.config.allowUnfree = true;

  microvm = {
    hypervisor = "qemu"; # most featureful of the eight: virtiofs, ballooning, mature
    vcpu = 24;
    mem = 32768;         # MB. qemu only faults pages in as the guest touches them,
                         # so this is a ceiling, not a reservation. Was 131072 on
                         # the 376GB cdssrv03; 32G each is the budget on cdssrv02,
                         # where the VMs share RAM with the model roster + ZFS ARC.

    # NIC: macvtap per-VM, defined in ../devpro, ../devhobby (unique id + MAC).

    # Host /nix/store shared read-only — the guest boots from a tiny squashfs and
    # sees every package the host has, so cache fixes (harmonia/lantian) apply to
    # the VM for free.
    shares = [{
      tag = "ro-store";
      proto = "virtiofs";
      source = "/nix/store";
      mountPoint = "/nix/.ro-store";
    }];

    # Writes to /nix/store inside the guest (nix develop, package installs) land in
    # this overlay instead of erroring on the read-only share.
    writableStoreOverlay = "/nix/.rw-store";

    # Persistent state. Images are created sparse on first boot under
    # /var/lib/microvms/<name>/ — thin-provisioned, they only occupy what is
    # actually written. Root stays the default read-only squashfs + tmpfs;
    # everything that matters lives on these volumes. Sizes are MB.
    volumes = [
      {
        image = "store-overlay.img";
        mountPoint = config.microvm.writableStoreOverlay;
        size = 102400; # 100G
      }
      {
        image = "home.img";
        mountPoint = "/home";
        size = 409600; # 400G — projects, ~/.omp, everything precious
      }
      {
        image = "var.img";
        mountPoint = "/var";
        size = 51200; # 50G — logs, state, melious.key; survives restarts
      }
    ];
  };

  networking.useDHCP = lib.mkDefault true;

  services.openssh = {
    enable = true;
    settings = {
      PasswordAuthentication = false;
      KbdInteractiveAuthentication = false;
      PermitRootLogin = "no";
    };
  };

  # mosh into the VM from roaming laptops (module opens UDP 60000-61000 itself).
  programs.mosh.enable = true;

  users.users.cristian = {
    isNormalUser = true;
    extraGroups = [ "wheel" ];
    # Personal devices, plus (2026-08-24, at the user's explicit request) the
    # cdssrv02 management key: that box supervises these VMs from time to time and
    # was otherwise stuck relaying every check through cdssrv03 or a laptop. This
    # DOES widen the jail — cdssrv02 is the fleet's most connected host — so the
    # cdssrv02 keys are the only non-personal ones here and they stay declarative
    # (this list), never a hand-edited authorized_keys, so `git log` remains the
    # record of who can enter.
    #
    # 2026-08-29: added cristian@cdssrv02 alongside root@cdssrv02. root's key only
    # serves things running as root on the host; interactive work there (herdr
    # panes, agents, plain ssh) runs as cristian and was hitting "Permission
    # denied (publickey)". Same trust boundary as the root key already crossed —
    # anyone who is root on cdssrv02 can read cristian's key anyway.
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJsaEWkVBhknpRAx+efz9vqhzfgs01h/Ea4aSZTbNMZi cristianstamateanu@Mac.localdomain"
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIL9++NSSIpNIbwpGl9vVgfsgsys7vmr39BWtifuk7+gx cristian@gpdp4-nix"
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINgNQXUGLVwx32MZiKHX7PBePecBsXgRf38CE9PndztD ish@iphone"
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJHsFFo1EsrztaYe4KmPzccn4nsNYJ4eaOg94GUEfxJf cristian@dell"
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAoh80aoxvU9I2EA1Kroxr4HHOrUsBZtjpbDQ5spMwYH cristian@gpd-mini"
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIF8ijJLCAgCz2hYYEK4QOO+Te0RiJuHgGondl7uWsrBP root@cdssrv02"
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJbFNNy7FwO4p9QTTWMGKcjAyZCcVu+N47HCRRCMOmjI cristian@cdssrv02"
    ];
  };
  security.sudo.wheelNeedsPassword = false;

  # The /home volume mounts after NixOS's user-dir creation on first boot, so the
  # home dir would be missing on a freshly provisioned VM. tmpfiles runs after
  # local-fs.target and repairs that on every boot.
  systemd.tmpfiles.rules = [
    "d /home/cristian 0700 cristian users -"
    "d /var/lib/sshd 0700 root root -"
  ];

  # Root is tmpfs — default /etc/ssh host keys would regenerate every reboot and
  # trip "REMOTE HOST IDENTIFICATION HAS CHANGED" on every laptop. Keep them on
  # the persistent /var volume instead (sshd generates them there on first start).
  services.openssh.hostKeys = [
    {
      path = "/var/lib/sshd/ssh_host_ed25519_key";
      type = "ed25519";
    }
  ];

  nix.settings = {
    experimental-features = [ "nix-command" "flakes" ];
    trusted-users = [ "root" "cristian" ];
    # Same cache stack as the host (most fetches short-circuit through the shared
    # store anyway; these cover builds the guest does itself).
    substituters = [
      "http://192.168.40.15:5000"
      "https://cache.nixos.org"
      "https://nix-community.cachix.org"
      "https://devenv.cachix.org"
    ];
    trusted-public-keys = [
      "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
      "cdssrv02-cache-1:FcBNFG0AyfpaNJEQs6ljXj6mO2/1MI5A0ryBDHGpFGI="
      "nix-community.cachix.org-1:mB9FSh9qf2dCimDSUo8Zy7bkq5CX+/rkCWyvRCYg3Fs="
      "devenv.cachix.org-1:w1cLUi8dv3hnoSPGAuibQv+f9TZLr6cv/Hm9XgU50cw="
      "lantian:EeAUQ+W+6r7EtwnmYjeVwx5kOGEBpjlBfPlzGlTNvHc="
      "cache.garnix.io:CTFPyKSLcx5RMJKfLo5EEPUObbA78b0YQ2DTCJXqr9g="
    ];
  };

  environment.systemPackages = with pkgs; [
    git
    vim
    htop
    btop
    curl
    wget
    jq
    rsync
    ripgrep
    fd

    # The laptops ssh in from Alacritty (TERM=alacritty; Ghostty also around).
    # Without these terminfo entries the session falls back to an 8-color TERM
    # and emacs quantizes theme hexes to ANSI (doom-ayu's navy bg rendered as
    # solid bright blue). Terminfo-only outputs — not the whole terminals.
    alacritty.terminfo
    ghostty.terminfo
  ];

  # tmux with colors done right — a bare `tmux` package defaults to
  # TERM=screen inside sessions (8 colors, same blue-emacs failure as above,
  # even with the terminfo fix outside tmux).
  programs.tmux = {
    enable = true;
    terminal = "tmux-256color";
    extraConfig = ''
      # Truecolor passthrough for the terminals the laptops actually use.
      set -ga terminal-features ',alacritty*:RGB,xterm-ghostty:RGB'
      set -ga terminal-overrides ',alacritty:Tc,xterm-ghostty:Tc'
    '';
  };
}
