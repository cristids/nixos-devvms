{ pkgs }:
let
  python = pkgs.python3.withPackages (p: [ p.psycopg ]);
  image = pkgs.dockerTools.buildLayeredImage {
    name = "assessment-python";
    tag = "pinned";
    contents = [ python pkgs.coreutils pkgs.bash ];
    config.Cmd = [ "${python}/bin/python3" ];
  };
  common = { lib, ... }: {
    virtualisation = {
      memorySize = 3072;
      cores = 2;
      diskSize = 16384;
      writableStore = true;
      restrictNetwork = true;
      vlans = [ 1 ];
    };
    # The test VLAN has no physical uplink. Slirp's host access is disabled too.
    networking.firewall.enable = true;
    networking.firewall.allowedTCPPorts = [ 22 8080 8081 8082 8085 8087 8088 8089 8091 8092 8443 ];
    services.openssh.enable = true;
    services.openssh.settings = {
      PermitRootLogin = "prohibit-password";
      PasswordAuthentication = false;
    };
    environment.systemPackages = with pkgs; [
      python curl openssl openssh git jq gnutar gzip coreutils iproute2
      procps sqlite postgresql nginx nixos-rebuild strace
    ];
    nix.settings = { experimental-features = [ "nix-command" "flakes" ]; substituters = lib.mkForce [ ]; };
    nix.nixPath = [ "nixpkgs=${pkgs.path}" ];
    environment.etc."lab/nixpkgs".source = pkgs.path;
    environment.etc."lab/python-image.tar".source = image;
    environment.etc."lab/build-support".source = pkgs.closureInfo {
      rootPaths = [ pkgs.stdenvNoCC pkgs.stdenv pkgs.shellcheck pkgs.makeWrapper ];
    };
    environment.etc."lab/seed-app.py".source = ./seed-app.py;
    environment.etc."lab/exec-safe.py".source = ./exec-safe.py;
    virtualisation.podman.enable = true;
    users.users.lab = { isNormalUser = true; uid = 1500; };
    systemd.tmpfiles.rules = [
      "d /srv/assessment 0755 root root -"
      "d /root/.config/assessment 0700 root root -"
    ];
    system.stateVersion = "26.05";
  };
in pkgs.testers.runNixOSTest {
  name = "harness-real-nixos-operations";
  globalTimeout = 8 * 60 * 60;
  skipTypeCheck = true;
  nodes = {
    admin = common;
    app = common;
    data = { ... }: {
      imports = [ common ];
      services.postgresql = {
        enable = true;
        enableTCPIP = true;
        authentication = pkgs.lib.mkOverride 10 ''
          local all postgres peer
          local all all peer
          host all all 127.0.0.1/32 scram-sha-256
          host all all 192.168.1.0/24 scram-sha-256
        '';
      };
      networking.firewall.allowedTCPPorts = [ 5432 ];
    };
  };
  testScript = ''
    import os
    import runpy
    start_all()
    for node in [admin, app, data]:
        node.wait_for_unit("multi-user.target")
    runpy.run_path(os.environ["HARNESS_OPERATIONS_SUPERVISOR"], init_globals={"machines": {"admin": admin, "app": app, "data": data}})
  '';
}
