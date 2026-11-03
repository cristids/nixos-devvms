{ config, lib, pkgs, ... }:

# omp + Melious as a cheap, sandboxed dev agent that Claude/Codex delegate simple
# tasks to (2026-11-03). Pieces:
#
#   omp-task             sandboxed one-shot omp run (bwrap): RW only the project dir,
#                        $HOME is an empty tmpfs (no ~/.ssh, ~/.codex, ~/.claude, gh,
#                        other projects), /var hidden (no /var/lib/*.key). Default plan
#                        mode: GLM 5.3 plans, Flash implements (--plan-yolo); --quick =
#                        Flash only. Prints the result, spend per model and
#                        `git diff --stat`; warns if HEAD moved.
#   context7-mcp         Context7 docs MCP, Nix-packaged (../pkgs/context7-mcp), run
#                        locally over stdio. Optional key: /var/lib/context7.key.
#   erpnext-mcp          our own stdio MCP for the devpro benches (./erpnext-mcp),
#                        test :8000 / UAT :8001 only, no delete. devpro only.
#   omp-dev-integrations user service: MCPs into ~/.codex/config.toml and
#                        ~/development/erpnext/.mcp.json (Claude + omp), the
#                        `delegate-to-omp` skill for Claude and Codex, and the global
#                        omp rules block in ~/.omp/agent/AGENTS.md.
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

  # Memory cap for test runs (2026-11-03): two global OOMs on devpro came from a
  # `node --test` dumping a cyclic fake DOM on an assertion failure (>20 GB).
  testMemoryMax = "6G";
  testScopeCmd = "systemd-run --user --scope -p MemoryMax=${testMemoryMax} --";

  # Appended (between markers) to ~/.claude/CLAUDE.md and ~/.codex/AGENTS.md by
  # omp-dev-integrations; the rest of those hand-edited files is left alone.
  testMemoryRule = ''
    # Test runs: memory cap

    - Wrap every test run (and other potentially memory-hungry commands) in
      `${testScopeCmd} <cmd>`, e.g.
      `${testScopeCmd} node --test`. A runaway is then killed inside its own
      scope instead of OOMing the VM and killing every tuios pane.
    - Why: on 2026-11-03 two global OOMs came from a `node --test` dumping a
      cyclic fake DOM on an assertion failure (>20 GB).
    - `omp-task` already runs under such a scope; don't add one inside it.
  '';

  # Global omp rules (Karpathy's four coding principles + the test-memory rule),
  # kept as a marked block in ~/.omp/agent/AGENTS.md by omp-dev-integrations.
  # omp loads that file as user-level context in every session (interactive and
  # omp-task, which binds it read-only into the sandbox).
  ompGlobalRules = ''
    # Coding rules

    1. Think before coding. State your assumptions explicitly. If the task is
       ambiguous, say so and pick the most conservative reading; don't silently
       guess. List the assumptions in your final summary.
    2. Simplicity first. Write the minimum code that solves the task: no
       speculative features, abstractions, config, or error handling for cases
       that cannot happen.
    3. Surgical changes. Touch only what the task needs and match the existing
       style. No drive-by refactors, reformatting or comment edits; clean up only
       your own leftovers.
    4. Goal-driven execution. Turn the task into a verifiable success criterion
       (a command), loop until it passes, then stop and report.

    # Tests and git

    - Inside `omp-task`: run tests directly; the whole run is already capped at
      ${testMemoryMax} RAM. Outside it, wrap test runs in
      `${testScopeCmd} <cmd>`. Why: on 2026-11-03 a `node --test` dumping a
      cyclic fake DOM OOMed the VM twice.
    - Never commit (or push); leave changes uncommitted for review.
  '';

  # omp-task specifics only; the general rules come from ~/.omp/agent/AGENTS.md.
  taskRules = ''
    You are running non-interactively inside a sandbox as a delegated worker.
    - Work only inside the current project directory. Other files under $HOME do not exist here.
    - NEVER run git commit, git push, git reset, git checkout of other branches, git stash or
      any other command that changes git history or refs. The caller reviews `git diff` and commits.
    - Do not ask questions (nobody answers): if something is ambiguous, take the most
      conservative reading and record it as an assumption.
    - Stay within the brief's "Allowed files"; do not touch anything listed as "Out of scope".
    - If you write a plan, put an "Assumptions" section in it.
    - Your final message (after implementing) must be a short report with exactly these
      headings: "Assumptions:" (every assumption from the plan and implementation, or
      "none"), "Files changed:", "Success criterion:" (the command and its result).
  '';

  planModel = "melious/glm-5.3";
  implModel = "melious/glm-5.3-flash";

  ompTask = pkgs.writeShellApplication {
    name = "omp-task";
    runtimeInputs = [ pkgs.coreutils pkgs.git pkgs.jq bubblewrap ];
    text = ''
      # Memory cap for the whole run (omp + the tests it runs). The scope is
      # created here, outside bwrap, because the sandbox has no user bus.
      # OOMPolicy=continue: a killed test must not take omp down with it.
      if [ -z "''${OMP_TASK_SCOPED:-}" ] && [ -S "''${XDG_RUNTIME_DIR:-/nonexistent}/bus" ]; then
        export OMP_TASK_SCOPED=1
        exec ${config.systemd.package}/bin/systemd-run --user --scope --quiet --collect \
          -p MemoryMax=${testMemoryMax} -p OOMPolicy=continue \
          -- "$0" "$@"
      elif [ -z "''${OMP_TASK_SCOPED:-}" ]; then
        echo "omp-task: no user bus, running without the ${testMemoryMax} memory scope" >&2
      fi

      usage() {
        cat >&2 <<'EOF'
      usage: omp-task [--cwd DIR] [--quick] [--max-cost USD] [--model M] "brief"
             omp-task [--cwd DIR] --exec CMD [ARGS...]   (run CMD in the same sandbox; for checks)

      Runs omp (Melious) once, non-interactively, sandboxed (bwrap): read-write only DIR
      (default: current dir; must be inside ~/development or ~/scratch), no ~/.ssh,
      ~/.codex, ~/.claude, gh auth, other projects or /var/lib keys. Network stays on.
      Modes:
        (default) plan: ${planModel} plans read-only, the plan is auto-approved, then
                  the implementer (--model, default ${implModel}) implements it.
                  --max-cost default 1.00 USD.
        --quick   ${implModel} only (--model overrides), no plan phase.
                  --max-cost default 0.50 USD.
      Prints omp's final message, spend per model, then `git diff --stat` and
      untracked files. omp must not commit; a moved HEAD is a warning (exit 4).
      EOF
        exit 2
      }

      dir=$PWD
      max_cost=""
      model=${implModel}
      quick=0
      exec_mode=0
      while [ $# -gt 0 ]; do
        case "$1" in
          --cwd) dir=''${2:?--cwd needs a directory}; shift 2 ;;
          --cwd=*) dir=''${1#--cwd=}; shift ;;
          --max-cost) max_cost=''${2:?--max-cost needs a value}; shift 2 ;;
          --max-cost=*) max_cost=''${1#--max-cost=}; shift ;;
          --model) model=''${2:?--model needs a value}; shift 2 ;;
          --model=*) model=''${1#--model=}; shift ;;
          --quick) quick=1; shift ;;
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
      if [ -z "$max_cost" ]; then
        if [ "$quick" = 1 ]; then max_cost=0.50; else max_cost=1.00; fi
      fi
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
      for f in models.yml config.yml AGENTS.md; do
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

      # plan mode: --plan-yolo starts read-only plan mode on the active model,
      # auto-approves the plan at its first resolve call and hands off to
      # --plan-yolo-into. Works with -p (verified on omp 18.16). (--plan only sets
      # the interactive /plan role; the plan phase here runs on --model.)
      if [ "$quick" = 1 ]; then
        mode=quick
        mode_args=(--model "$model")
      else
        mode=plan
        mode_args=(--model ${planModel} --plan ${planModel} --plan-yolo --plan-yolo-into "$model")
      fi

      # JSON events go to a file; the summary below is extracted from them.
      events=$stage/events.jsonl
      rc=0
      bwrap "''${args[@]}" -- ${sw}/omp -p --mode json \
        --no-session --no-title \
        --approval-mode yolo \
        "''${mode_args[@]}" \
        --max-cost "$max_cost" \
        --append-system-prompt ${pkgs.writeText "omp-task-rules.md" taskRules} \
        "$spec" > "$events" || rc=$?

      jq -rs '[.[] | select(.type == "message_end" and .message.role == "assistant")]
        | last | (.message.content // []) | map(select(.type == "text") | .text) | join("")' \
        "$events" 2>/dev/null || echo "omp-task: could not parse omp output" >&2
      echo
      echo "── omp-task: spend by model (USD; mode: $mode) ──"
      jq -rs '[.[] | select(.type == "message_end" and .message.role == "assistant")]
        | group_by(.message.model)[]
        | "\(.[0].message.model): \(length) turns, \((map(.message.usage.cost.total // 0) | add) * 10000 | round / 10000)"' \
        "$events" 2>/dev/null || true
      jq -r 'select(.type == "notice" and .source == "plan-yolo") | .message' "$events" 2>/dev/null || true
      if [ "$mode" = plan ] && ! jq -e 'select(.type == "notice" and .source == "plan-yolo")' "$events" >/dev/null 2>&1; then
        echo "WARNING: plan was never approved, so nothing was implemented." >&2
      fi
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
    description: Hand small, clearly specified coding chores (tests, lint fixes, boilerplate, renames, small scripts) to omp, a cheap sandboxed agent on this VM (Melious GLM 5.3 plans, GLM 5.3 Flash implements), to save tokens. Use when the task fits in a short brief with a command that proves it is done.
    ---

    # Delegate to omp (`omp-task`)

    `omp-task` runs omp once, non-interactively, inside a bwrap sandbox: it can write
    only the project directory you give it, sees no ~/.ssh, ~/.codex, ~/.claude, gh
    auth or other projects, and stops at a spend cap. It has the context7 (library
    docs) MCP and, on devpro, the ERPNext test/UAT MCP. omp follows global coding
    rules (assumptions stated, minimal and surgical changes, loop until the success
    criterion passes) from ~/.omp/agent/AGENTS.md.

    ## Delegate
    - writing or extending unit tests for existing code
    - lint/format/type fixes with a clear checker command
    - boilerplate (new module/class/CLI skeleton from an explicit spec)
    - mechanical renames and small refactors
    - small standalone scripts

    ## Do not delegate
    - debugging failures whose cause is unclear
    - design or architecture decisions
    - NixOS / deploy / production config, anything on prod data
    - security-sensitive code (auth, secrets, permissions)

    ## Brief (always this format)
    ```
    Goal: <what to build or change, one paragraph>
    Success criterion: <command that must pass>
    Allowed files: <files/dirs omp may create or edit>
    Out of scope: <what must not change>
    ```

    ## Run
    ```sh
    omp-task --cwd <project dir under ~/development or ~/scratch> "<brief>"
    ```
    - default = plan mode: GLM 5.3 plans read-only, then GLM 5.3 Flash implements;
      cap 1.00 USD. Use for anything beyond a one-spot edit.
    - `--quick`: Flash only, no plan phase; cap 0.50 USD. Trivial edits only.
    - `--model M` sets the implementer; `--max-cost USD` overrides the cap.

    The output ends with omp's report (with its assumptions), spend per model,
    `git diff --stat` and untracked files.

    ## Then, yourself, before accepting
    1. Run the success criterion yourself.
    2. Review `git -C <dir> diff` and the untracked files: only allowed files, nothing
       extra. Read omp's assumptions. omp output is not trusted.
    3. Fix or discard (`git checkout -- <files>`), and commit only when it is right.

    Never let omp commit or push (the sandbox makes .git read-only and omp-task warns
    if HEAD moved, exit 4). Exit 3 means the spend cap was reached; narrow the brief
    instead of raising the cap.
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


    def upsert_block(p: Path, begin: str, end: str, src: str) -> None:
        """Keep a marked block in a hand-edited file; text outside it is left alone."""
        block = begin + "\n" + Path(src).read_text().rstrip("\n") + "\n" + end + "\n"
        p.parent.mkdir(parents=True, exist_ok=True)
        old = p.read_text() if p.exists() else ""
        if begin in old and end in old:
            pre, rest = old.split(begin, 1)
            post = rest.split(end, 1)[1].lstrip("\n")
            new = pre + block + post
        else:
            new = old + ("" if old.endswith("\n\n") or not old else ("\n" if old.endswith("\n") else "\n\n")) + block
        write_if_changed(p, new, 0o644)


    # Test-run memory rule: a marked block in the hand-edited instruction files.
    for p in (home / ".claude" / "CLAUDE.md", codex / "AGENTS.md"):
        upsert_block(
            p,
            "<!-- BEGIN nixos-devvms test-memory-cap (managed, edits are overwritten) -->",
            "<!-- END nixos-devvms test-memory-cap -->",
            "${pkgs.writeText "test-memory-rule.md" testMemoryRule}",
        )

    # Global omp rules: a marked block in ~/.omp/agent/AGENTS.md (user text kept).
    upsert_block(
        home / ".omp" / "agent" / "AGENTS.md",
        "<!-- BEGIN nixos-devvms omp-rules (managed, edits are overwritten) -->",
        "<!-- END nixos-devvms omp-rules -->",
        "${pkgs.writeText "omp-global-rules.md" ompGlobalRules}",
    )

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
