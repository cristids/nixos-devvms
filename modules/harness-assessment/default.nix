{ config, pkgs, hermesPkg, vibePkg, piAdminSource, piAdminBaseline, operationsPkg, ... }:
let
  contenders = pkgs.callPackage ../../pkgs/harness-contenders { };
  piPkg = pkgs.callPackage ../../pkgs/pi-coding-agent { };
  checks = pkgs.runCommand "harness-assessment-tests" { nativeBuildInputs = [ pkgs.python3 ]; } ''
    cp ${./assess.py} assess.py
    cp ${./test_assess.py} test_assess.py
    python3 -m unittest test_assess
    touch $out
  '';
  bench = pkgs.writeShellApplication {
    name = "harness-assess";
    runtimeInputs = [ pkgs.python3 pkgs.bubblewrap pkgs.socat pkgs.coreutils pkgs.git pkgs.curl pkgs.bash
      piPkg hermesPkg vibePkg contenders.opencode contenders.goose ];
    text = ''
      # Build-time boundary tests: ${checks}
      export HARNESS_PI_BASELINE=${piAdminBaseline}
      export HARNESS_PI_SOURCE=${piAdminSource}
      export HARNESS_PI_BIN=${piPkg}/bin/pi
      exec python3 ${./assess.py} "$@"
    '';
  };
in {
  environment.systemPackages = [ hermesPkg vibePkg contenders.opencode contenders.goose bench operationsPkg ];
  services.pi-admin = {
    enable = true;
    reviewer.hostDescription = "devhobby, an isolated synthetic harness assessment VM";
    configRepo = "/home/cristian/development/nixos-devvms";
    package = piPkg;
    bubblewrap = config.ompDevAgent.bubblewrap;
    searxng.url = "http://192.168.40.28:8080";
    tune.harvest.enable = false;
    agents.intro = "# pi-admin on devhobby\nIsolated hobby and harness-assessment VM. No production credentials or business data belong here.";
  };
}
