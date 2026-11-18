{ pkgs }:
let
  checks = pkgs.runCommand "claude-review-boundary-tests" { nativeBuildInputs = [ pkgs.python3 ]; } ''
    cp ${./claude_review.py} claude_review.py
    cp ${./test_claude_review.py} test_claude_review.py
    python3 -m unittest test_claude_review
    touch $out
  '';
in pkgs.writeShellApplication {
  name = "harness-claude-review";
  runtimeInputs = [ pkgs.python3 ];
  text = ''
    # Boundary checks: ${checks}
    exec python3 ${./claude_review.py} "$@"
  '';
}
