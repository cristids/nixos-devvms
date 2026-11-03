# erpnext-mcp — our own small stdio MCP server for the devpro ERPNext benches
# (test :8000, UAT :8001; allowlist hard-coded in erpnext_mcp.py). The unit tests
# run as part of the build, so a red test fails the rebuild instead of shipping.
{ python3, runCommand, writeShellApplication }:

let
  py = python3.withPackages (ps: [ ps.fastmcp ps.httpx ]);
  pyTest = python3.withPackages (ps: [ ps.fastmcp ps.httpx ps.pytest ]);
  server = ./erpnext_mcp.py;

  tests = runCommand "erpnext-mcp-tests" { } ''
    cp ${server} erpnext_mcp.py
    cp ${./test_erpnext_mcp.py} test_erpnext_mcp.py
    HOME=$TMPDIR ${pyTest}/bin/python3 -m pytest -q -p no:cacheprovider test_erpnext_mcp.py
    touch $out
  '';
in
writeShellApplication {
  name = "erpnext-mcp";
  # Referencing `tests` makes the test run a build dependency of the binary.
  text = ''
    # tests: ${tests}
    exec ${py}/bin/python3 -I ${server} "$@"
  '';
}
