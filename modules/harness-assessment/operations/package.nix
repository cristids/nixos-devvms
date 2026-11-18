{ pkgs, piSource, piBaseline, hermes, vibe, omp }:
let
  contenders = pkgs.callPackage ../../../pkgs/harness-contenders { };
  qwen = pkgs.callPackage ../../../pkgs/qwen-code { };
  openclaw = pkgs.callPackage ../../../pkgs/openclaw-assessment { };
  letta = pkgs.callPackage ../../../pkgs/letta-code { };
  pi = pkgs.callPackage ../../../pkgs/pi-coding-agent { };
  dsh = pkgs.callPackage ../../../pkgs/dsh { };
  driver = (import ./lab.nix { inherit pkgs; }).driver;
  checks = pkgs.runCommand "harness-operations-unit-tests" { nativeBuildInputs = [ pkgs.python3 pkgs.strace pkgs.bash pkgs.coreutils ]; } ''
    cp ${./.}/*.py .
    python3 -m unittest test_operations
    touch $out
  '';
in pkgs.writeShellApplication {
  name = "harness-operations";
  runtimeInputs = [ pkgs.python3 pkgs.bubblewrap pkgs.socat pkgs.coreutils
    pkgs.git pkgs.curl pkgs.bash pi dsh hermes vibe omp letta openclaw qwen contenders.opencode contenders.goose ];
  text = ''
    # Tested guard and task catalogue: ${checks}
    export HARNESS_PI_SOURCE=${piSource}
    export HARNESS_PI_BASELINE=${piBaseline}
    export HARNESS_PI_BIN=${pi}/bin/pi
    export HARNESS_OPERATIONS_DRIVER=${driver}
    exec python3 ${../.}/operations/launch.py "$@"
  '';
}
