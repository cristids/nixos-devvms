{ config, lib, pkgs, ... }:

# omp + Melious as a cheap, sandboxed dev agent that Claude/Codex delegate simple
# tasks to (2026-11-03). Pieces:
#
#   omp-task             sandboxed one-shot omp run (bwrap): RW only the project dir,
#                        $HOME is an empty tmpfs (no ~/.ssh, ~/.codex, ~/.claude, gh,
#                        other projects), /var hidden (no /var/lib/*.key). Prints the
#                        result + `git diff --stat`; warns if HEAD moved.
#   context7-mcp         Context7 docs MCP, Nix-packaged (../pkgs/context7-mcp), run
#                        locally over stdio. Optional key: /var/lib/context7.key.
#   erpnext-mcp          our own stdio MCP for the devpro benches (./erpnext-mcp),
#                        test :8000 / UAT :8001 only, no delete. devpro only.
#   omp-dev-integrations user service: MCPs into ~/.codex/config.toml and
#                        ~/development/erpnext/.mcp.json (Claude + omp), plus the
#                        `delegate-to-omp` skill for Claude and Codex.
#
# Secrets (never in the store/repo): /var/lib/melious.key (omp-models-seed in
# agents.nix renders ~/.omp/agent/models.yml from it) and, on devpro,
# /var/lib/erpnext-mcp.env owned by cristian, mode 0600:
#   ERPNEXT_TEST_KEY=…  ERPNEXT_TEST_SECRET=…  ERPNEXT_UAT_KEY=…  ERPNEXT_UAT_SECRET=…
# (API key/secret of the `mcp-dev` user on test.localhost / uat.localhost).
let
  cfg = config.ompDevAgent;
  home = "/home/cristian";

  context7Pkg = pkgs.callPackage ../pkgs/context7-mcp { };
  erpnextMcp = pkgs.callPackage ./erpnext-mcp { };

  # Stable command paths for the user configs, so a GC never leaves them dangling.
  sw = "/run/current-system/sw/bin";

  # Same name as the upstream binary; adds the optional API key (higher rate
  # limits) from /var/lib/context7.key when the caller can read it. Inside the
  # omp-task sandbox /var is hidden, so it runs anonymously there.
  context7Mcp = pkgs.writeShellApplication {
    name = "context7-mcp";
    text = ''
      key_file=/var/lib/context7.key
      if [ -z "''${CONTEXT7_API_KEY:-}" ] && [ -r "$key_file" ]; then
        CONTEXT7_API_KEY=$(cat "$key_file")
        export CONTEXT7_API_KEY
      fi
      exec ${context7Pkg}/bin/context7-mcp "$@"
    '';
  };

  taskRules = ''
    You are running non-interactively inside a sandbox as a delegated worker.
    - Work only inside the current project directory. Other files under $HOME do not exist here.
    - NEVER run git commit, git push, git reset, git checkout of other branches, git stash or
      any other command that changes git history or refs. Leave all changes uncommitted;
      the caller reviews `git diff` and commits.
    - Do not ask questions: make reasonable assumptions, finish the task, then run the
      acceptance command from the task (if any) and fix until it passes.
    - End with a short report: files changed, acceptance command and its result.
  '';

  ompTask = pkgs.writeShellApplication {
    name = "omp-task";
    runtimeInputs = [ pkgs.coreutils pkgs.git pkgs.jq bubblewrap ];
    text = ''
      usage() {
        cat >&2 <<'EOF'
      usage: omp-task [--cwd DIR] [--max-cost USD] [--model M] "spec"
             omp-task [--cwd DIR] --exec CMD [ARGS...]   (run CMD in the same sandbox; for checks)

      Runs omp (Melious) once, non-interactively, sandboxed (bwrap): read-write only DIR
      (default: current dir; must be inside ~/development or ~/scratch), no ~/.ssh,
      ~/.codex, ~/.claude, gh auth, other projects or /var/lib keys. Network stays on.
      Defaults: --model melious/glm-5.3-flash, --max-cost 0.50 (USD).
      Prints omp's final message, then `git diff --stat`. omp must not commit;
      a moved HEAD is reported as a warning (exit 4).
      EOF
        exit 2
      }

      dir=$PWD
      max_cost=0.50
      model=melious/glm-5.3-flash
      exec_mode=0
      while [ $# -gt 0 ]; do
        case "$1" in
          --cwd) dir=''${2:?--cwd needs a directory}; shift 2 ;;
          --cwd=*) dir=''${1#--cwd=}; shift ;;
          --max-cost) max_cost=''${2:?--max-cost needs a value}; shift 2 ;;
          --max-cost=*) max_cost=''${1#--max-cost=}; shift ;;
          --model) model=''${2:?--model needs a value}; shift 2 ;;
          --model=*) model=''${1#--model=}; shift ;;
          --exec) exec_mode=1; shift; break ;;
          -h|--help) usage ;;
          --) shift; break ;;
          -*) echo "omp-task: unknown option $1" >&2; usage ;;
          *) break ;;
        esac
      done
      [ $# -gt 0 ] || usage

      home=${home}
      dir=$(realpath -e -- "$dir") || { echo "omp-task: no such directory" >&2; exit 2; }
      [ -d "$dir" ] || { echo "omp-task: $dir is not a directory" >&2; exit 2; }
      case "$dir/" in
        "$home"/development/?*/|"$home"/scratch/?*/) ;;
        *) echo "omp-task: $dir must be a project directory inside ~/development or ~/scratch" >&2; exit 2 ;;
      esac
      case "$max_cost" in
        ""|*[!0-9.]*) echo "omp-task: --max-cost must be a number (USD)" >&2; exit 2 ;;
      esac

      agent=$home/.omp/agent
      if [ ! -r "$agent/models.yml" ] && [ "$exec_mode" = 0 ]; then
        echo "omp-task: $agent/models.yml missing — install /var/lib/melious.key first" >&2
        exit 2
      fi

      stage=$(mktemp -d "''${XDG_RUNTIME_DIR:-/tmp}/omp-task.XXXXXX")
      trap 'rm -rf "$stage"' EXIT
      chmod 700 "$stage"

      # MCP servers for the sandboxed omp (user-level omp config, which outranks
      # project .mcp.json files). /var stays hidden: the ERPNext credentials are read
      # here, OUTSIDE the sandbox, and a private copy is bound read-only where only the
      # erpnext MCP is pointed at it (ERPNEXT_MCP_ENV_FILE in its `env`, not in the
      # agent's environment). omp runs its stdio servers as the same user in the same
      # sandbox, so the agent could still read that copy — tradeoff accepted: test/UAT
      # mcp-dev creds only, never prod, never the Melious key file.
      erp_copy=""
      if ${lib.boolToString cfg.erpnext.enable} && [ -r /var/lib/erpnext-mcp.env ]; then
        install -m 600 /var/lib/erpnext-mcp.env "$stage/erpnext-mcp.env"
        erp_copy=$agent/erpnext-mcp.env
      fi
      jq -n --arg sw "${sw}" --arg envf "''${erp_copy:-/nonexistent}" \
        --argjson withErp ${lib.boolToString cfg.erpnext.enable} '
        {mcpServers: ({context7: {type: "stdio", command: ($sw + "/context7-mcp")}}
          + (if $withErp then {erpnext: {type: "stdio", command: ($sw + "/erpnext-mcp"),
                                         env: {ERPNEXT_MCP_ENV_FILE: $envf}}}
             else {} end))}' > "$stage/mcp.json"
      chmod 600 "$stage/mcp.json"

      args=(
        --die-with-parent --unshare-pid --unshare-ipc --unshare-uts --new-session
        --clearenv
        --ro-bind /nix/store /nix/store
        --ro-bind /run/current-system /run/current-system
        --ro-bind /etc /etc
        --ro-bind /bin /bin
        --ro-bind /usr /usr
        --proc /proc
        --dev /dev
        --tmpfs /tmp
        --tmpfs /home
        --dir "$home"
        --dir "$agent"
        --ro-bind "$stage/mcp.json" "$agent/mcp.json"
      )
      if [ -n "$erp_copy" ]; then
        args+=(--ro-bind "$stage/erpnext-mcp.env" "$erp_copy")
      fi
      # `nix develop` / `nix shell` for project toolchains go through the daemon.
      if [ -S /nix/var/nix/daemon-socket/socket ]; then
        args+=(--ro-bind /nix/var/nix/daemon-socket /nix/var/nix/daemon-socket --setenv NIX_REMOTE daemon)
      fi
      for f in models.yml config.yml; do
        [ -r "$agent/$f" ] && args+=(--ro-bind "$agent/$f" "$agent/$f")
      done
      args+=(--bind "$dir" "$dir")
      # Belt and braces for "omp must not commit": git metadata is read-only.
      if [ -e "$dir/.git" ]; then
        args+=(--ro-bind "$dir/.git" "$dir/.git")
      fi
      args+=(
        --chdir "$dir"
        --setenv HOME "$home"
        --setenv USER "$(id -un)"
        --setenv PATH ${sw}
        --setenv TERM dumb
        --setenv LANG C.UTF-8
        --setenv TMPDIR /tmp
        --setenv SSL_CERT_FILE /etc/ssl/certs/ca-certificates.crt
        --setenv NIX_SSL_CERT_FILE /etc/ssl/certs/ca-certificates.crt
        --setenv GIT_CONFIG_NOSYSTEM 1
        --setenv OMP_MCP_TIMEOUT_MS 60000
      )

      if [ "$exec_mode" = 1 ]; then
        exec bwrap "''${args[@]}" -- "$@"
      fi

      spec=$*
      head_before=$(git -C "$dir" rev-parse -q --verify HEAD 2>/dev/null || true)

      rc=0
      bwrap "''${args[@]}" -- ${sw}/omp -p \
        --no-session --no-title \
        --approval-mode yolo \
        --model "$model" \
        --max-cost "$max_cost" \
        --append-system-prompt ${pkgs.writeText "omp-task-rules.md" taskRules} \
        "$spec" || rc=$?

      echo
      echo "── omp-task: exit $rc (3 = --max-cost $max_cost USD reached) ──"
      if git -C "$dir" rev-parse -q --git-dir >/dev/null 2>&1; then
        echo "── git diff --stat (uncommitted changes in $dir) ──"
        git -C "$dir" diff --stat
        untracked=$(git -C "$dir" ls-files --others --exclude-standard)
        if [ -n "$untracked" ]; then
          echo "── untracked files ──"
          printf '%s\n' "$untracked"
        fi
        head_after=$(git -C "$dir" rev-parse -q --verify HEAD 2>/dev/null || true)
        if [ "$head_before" != "$head_after" ]; then
          echo "WARNING: HEAD moved during the run ($head_before -> $head_after): omp must not commit." >&2
          [ "$rc" = 0 ] && rc=4
        fi
      fi
      exit "$rc"
    '';
  };

  skillMd = pkgs.writeText "delegate-to-omp-SKILL.md" ''
    ---
    name: delegate-to-omp
    description: Hand small, clearly specified coding chores (tests, lint fixes, boilerplate, renames, small scripts) to omp, a cheap sandboxed agent on this VM (Melious GLM 5.3 Flash), to save tokens. Use when the task fits in a one-page spec with a command that proves it is done.
    ---

    # Delegate to omp (`omp-task`)

    `omp-task` runs omp once, non-interactively, inside a bwrap sandbox: it can write
    only the project directory you give it, sees no ~/.ssh, ~/.codex, ~/.claude, gh
    auth or other projects, and stops at a spend cap (default 0.50 USD). It has the
    context7 (library docs) MCP and, on devpro, the ERPNext test/UAT MCP.

    ## Delegate
    - writing or extending unit tests for existing code
    - lint/format/type fixes with a clear checker command
    - boilerplate (new module/class/CLI skeleton from an explicit spec)
    - mechanical renames and small refactors
    - small standalone scripts

    The spec must fit in about one page and end with an acceptance command that
    passes only when the work is done.

    ## Do not delegate
    - debugging failures whose cause is unclear
    - design or architecture decisions
    - NixOS / deploy / production config, anything on prod data
    - security-sensitive code (auth, secrets, permissions)

    ## How
    ```sh
    omp-task --cwd <project dir under ~/development or ~/scratch> "<spec>
    Acceptance: <command>"
    # options: --max-cost 0.50   --model melious/glm-5.3   (slower, smarter)
    ```
    Then, yourself:
    1. Review `git -C <dir> diff` (and untracked files) — omp output is not trusted.
    2. Re-run the acceptance command.
    3. Fix or discard (`git checkout -- <files>`), and commit only when it is right.

    Never let omp commit or push (the sandbox makes .git read-only and omp-task warns
    if HEAD moved). Exit 3 means the spend cap was reached; narrow the spec instead
    of raising the cap.
  '';

  integrationsScript = pkgs.writers.writePython3 "omp-dev-integrations"
    { libraries = [ pkgs.python3Packages.tomlkit ]; flakeIgnore = [ "E501" ]; } ''
    import json
    import os
    import shutil
    import sys
    from pathlib import Path

    import tomlkit

    home = Path.home()
    with_erp = sys.argv[1] == "1"
    sw = "${sw}"
    servers = {"context7": {"type": "stdio", "command": f"{sw}/context7-mcp", "args": []}}
    if with_erp:
        servers["erpnext"] = {"type": "stdio", "command": f"{sw}/erpnext-mcp", "args": []}


    def write_if_changed(path: Path, text: str, mode: int = 0o600) -> None:
        if path.exists() and path.read_text() == text:
            return
        if path.exists():
            shutil.copy2(path, path.with_name(path.name + ".bak-omp-dev"))
        tmp = path.with_name(path.name + ".tmp-omp-dev")
        tmp.write_text(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        print(f"updated {path}")


    # Claude Code + omp: project .mcp.json in the ERPNext workspace (gitignored there).
    erp_dir = home / "development" / "erpnext"
    if with_erp and erp_dir.is_dir():
        p = erp_dir / ".mcp.json"
        data = {}
        if p.exists():
            try:
                data = json.loads(p.read_text() or "{}")
            except ValueError:
                print(f"{p}: invalid JSON, leaving it alone", file=sys.stderr)
                data = None
        if data is not None:
            data.setdefault("mcpServers", {}).update(servers)
            write_if_changed(p, json.dumps(data, indent=2) + "\n", 0o644)

    # Codex: [mcp_servers.*] in ~/.codex/config.toml, other keys untouched.
    codex = home / ".codex"
    codex.mkdir(exist_ok=True)
    cfg_path = codex / "config.toml"
    doc = tomlkit.parse(cfg_path.read_text()) if cfg_path.exists() else tomlkit.document()
    if "mcp_servers" not in doc:
        doc["mcp_servers"] = tomlkit.table(is_super_table=True)
    for name, srv in servers.items():
        t = tomlkit.table()
        t["command"] = srv["command"]
        doc["mcp_servers"][name] = t
    write_if_changed(cfg_path, tomlkit.dumps(doc))

    # delegate-to-omp skill for Claude and Codex.
    skill = Path("${skillMd}").read_text()
    for base in (home / ".claude" / "skills", codex / "skills"):
        d = base / "delegate-to-omp"
        d.mkdir(parents=True, exist_ok=True)
        write_if_changed(d / "SKILL.md", skill, 0o644)
  '';

  bubblewrap = config.ompDevAgent.bubblewrap;
in
{
  options.ompDevAgent = {
    erpnext.enable = lib.mkEnableOption "the ERPNext MCP (devpro benches)";
    bubblewrap = lib.mkOption {
      type = lib.types.package;
      description = "bwrap used by omp-task (the pinned one from agents.nix).";
    };
  };

  config = {
    environment.systemPackages = [ ompTask context7Mcp ]
      ++ lib.optional cfg.erpnext.enable erpnextMcp;

    systemd.tmpfiles.rules = [ "d ${home}/scratch 0755 cristian users -" ];

    systemd.user.services.omp-dev-integrations = {
      description = "Wire context7/ERPNext MCPs and the delegate-to-omp skill into Claude, Codex, omp";
      wantedBy = [ "default.target" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = "${integrationsScript} ${if cfg.erpnext.enable then "1" else "0"}";
      };
    };
  };
}
