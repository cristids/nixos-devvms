{ lib, modulesPath, pkgs, ... }:

# Shared base for the dev VMs. The guests are conventional libvirt VMs backed by
# sparse ZFS zvols on cdssrv02; per-VM CPU, memory, network, and storage policy is
# kept in the host's libvirt definitions.
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
    "${modulesPath}/profiles/qemu-guest.nix"
    ../../modules/emacs.nix
    ../../modules/emacs-mini.nix
    ../../modules/dev-tooling.nix
    ../../modules/agents.nix
  ];

  system.stateVersion = "25.11";
  time.timeZone = "Europe/Bucharest";
  nixpkgs.config.allowUnfree = true;

  boot = {
    growPartition = true;
    loader = {
      systemd-boot.enable = true;
      efi.canTouchEfiVariables = false;
    };
  };

  fileSystems = {
    "/" = {
      device = "/dev/disk/by-label/nixos";
      fsType = "ext4";
      autoResize = true;
    };
    "/boot" = {
      device = "/dev/disk/by-label/ESP";
      fsType = "vfat";
    };
    "/mnt/host-share" = {
      device = "hostshare";
      fsType = "virtiofs";
      options = [
        "ro"
        "nofail"
        "x-systemd.automount"
        "x-systemd.idle-timeout=60"
      ];
    };
  };

  services.qemuGuest.enable = true;
  zramSwap = {
    enable = true;
    memoryPercent = 25;
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

  systemd.tmpfiles.rules = [
    "d /home/cristian 0700 cristian users -"
    "d /var/lib/sshd 0700 root root -"
    "d /mnt/host-share 0755 root root -"
    "L+ /home/cristian/host-share - - - - /mnt/host-share"
  ];

  # Keep the historical path so migration can preserve the current host keys and
  # clients do not see an SSH identity change at cutover.
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
      # macvtap guests reach cdssrv02 through its vmshim0 address; ARP-flux
      # protection intentionally prevents using the host's eno1np0 address.
      "http://192.168.40.28:5000"
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
    yazi # fast terminal file manager; fleet default

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
