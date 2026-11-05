{ lib, python3Packages, honcho, git, redis, nodejs, yarn }:

# frappe-bench CLI (`bench`), bake-off 2026-11-05 (devhobby throwaway benches).
# devpro runs the same version as a uv tool; here it is a Nix package (no
# ~/.local/bin installs). Same version as devpro (5.31.0) so bench dirs copied from
# devpro behave identically. Upstream pins with ~=; nixpkgs versions are relaxed.
python3Packages.buildPythonApplication rec {
  pname = "frappe-bench";
  version = "5.31.0";
  format = "wheel";
  src = python3Packages.fetchPypi {
    pname = "frappe_bench";
    inherit version format;
    dist = "py3";
    python = "py3";
    hash = "sha256-hyr1Pb3GC0twm1UyANBfQCwdZKuxIJwWApBEVZJIdxg=";
  };
  dependencies = with python3Packages; [
    click gitpython (toPythonModule honcho) jinja2 python-crontab requests semantic-version
    setuptools uv
  ];
  pythonRelaxDeps = true;
  # bench shells out to git, redis-server (bench start), node/yarn (asset builds).
  makeWrapperArgs = [ "--prefix" "PATH" ":" (lib.makeBinPath [ honcho git redis nodejs yarn ]) ];
  doCheck = false;
  pythonImportsCheck = [ "bench" ];
  meta = {
    description = "CLI to manage Frappe deployments";
    homepage = "https://github.com/frappe/bench";
    license = lib.licenses.gpl3Only;
    mainProgram = "bench";
  };
}
