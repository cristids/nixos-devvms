# erpnext-mcp — our own small stdio MCP server for the devpro ERPNext dev benches.
# Environments (bench → port → sites) are discovered from
# ~/development/erpnext/benches at call time; see erpnext_mcp.py.
# Also ships `erpnext-mcp-provision` (API user + keys on every served site, keys in
# /var/lib/erpnext-mcp/credentials.json). The unit tests run as part of the build,
# so a red test fails the rebuild instead of shipping.
{ python3, runCommand, writeShellApplication, symlinkJoin }:

let
  py = python3.withPackages (ps: [ ps.fastmcp ps.httpx ]);
  pyTest = python3.withPackages (ps: [ ps.fastmcp ps.httpx ps.pytest ]);
  src = runCommand "erpnext-mcp-src" { } ''
    mkdir -p $out
    cp ${./erpnext_mcp.py} $out/erpnext_mcp.py
    cp ${./provision.py} $out/provision.py
    cp ${./provision_site.py} $out/provision_site.py
  '';

  tests = runCommand "erpnext-mcp-tests" { } ''
    cp ${src}/erpnext_mcp.py erpnext_mcp.py
    cp ${./test_erpnext_mcp.py} test_erpnext_mcp.py
    HOME=$TMPDIR ${pyTest}/bin/python3 -m pytest -q -p no:cacheprovider test_erpnext_mcp.py
    cp ${src}/provision.py ${src}/provision_site.py .
    ${py}/bin/python3 -m py_compile provision.py provision_site.py
    touch $out
  '';

  server = writeShellApplication {
    name = "erpnext-mcp";
    # Referencing `tests` makes the test run a build dependency of the binary.
    text = ''
      # tests: ${tests}
      exec ${py}/bin/python3 -I ${src}/erpnext_mcp.py "$@"
    '';
  };

  provision = writeShellApplication {
    name = "erpnext-mcp-provision";
    text = ''
      # tests: ${tests}
      # Same direnv as the bench units (system one, with nix-direnv configured).
      export DIRENV=''${DIRENV:-/run/current-system/sw/bin/direnv}
      exec ${py}/bin/python3 -I ${src}/provision.py "$@"
    '';
  };
in
symlinkJoin {
  name = "erpnext-mcp";
  paths = [ server provision ];
}
