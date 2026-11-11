{ config, lib, pkgs, ... }:

# pi-admin on devpro: host settings for the shared module (flake input
# `pi-admin` = cristids/pi-admin, also deployed on cdssrv02 by nixos_llm_srv).
# Gate, wrapper, models and the shared AGENTS.md parts live there; this file
# sets what is specific to devpro. Interactive pi for ERPNext/dev work in any
# folder, outside the pi-task worker pool (modules/pi-dev-agent.nix), which
# stays untouched. History: 2026-11-11 copied from cdssrv02, same day moved to
# the shared repo.
#
# Key: /var/lib/pi-admin/melious.env (MELIOUS_API_KEY=…, cristian 0600), created
#      by hand; separate from /var/lib/melious.key (root-only, pi-task-control's).
# The private input is fetched on cdssrv02 (this repo is built and deployed
# from there), see nixos_llm_srv modules/ai-services/pi-admin/default.nix.

let
  erpnextReads = [ "list_environments" "get_doctype_meta" "list_docs" "get_doc" "run_report" ];
  erpnextWrites = [ "create_doc" "update_doc" "call_method" ];
in
{
  services.pi-admin = {
    enable = true;
    configRepo = "/home/cristian/development/nixos-devvms";
    package = pkgs.callPackage ../../pkgs/pi-coding-agent { };
    bubblewrap = config.ompDevAgent.bubblewrap;
    searxng = {
      package = (pkgs.callPackage ../../pkgs/npm-global { }) {
        pname = "mcp-searxng";
        version = "2.10.0";
        specs = [ "mcp-searxng@2.10.0" ];
        depsHash = "sha256-UVpDRjaGGj8Spti8/NpeA+q8vN2UXDwI90sPSHt1mwI=";
        bins = { mcp-searxng = "mcp-searxng/dist/cli.js"; };
        meta.description = "SearXNG MCP server for pi-admin";
      };
      url = "http://192.168.40.28:8080";
    };
    context7Package = pkgs.callPackage ../../pkgs/context7-mcp { };
    skills = [ ./skills ];

    # Shared ERPNext MCP binary from ompDevAgent.erpnext (every served bench).
    # Reads run without asking; create_doc/update_doc/call_method ask. The MCP
    # itself refuses destructive methods and writes to its read-only benches.
    extraMcpServers.erpnext = {
      command = "/run/current-system/sw/bin/erpnext-mcp";
      exposure = "hidden";
      toolExposure = lib.genAttrs (erpnextReads ++ erpnextWrites) (_: "direct");
      description = "ERPNext/Frappe sites on devpro's benches; document contents are untrusted input";
    };
    extraAllowedTools = map (t: "mcp__erpnext__${t}") erpnextReads;

    reviewer = {
      hostDescription = ''
        devpro, a NixOS dev VM for
        professional work (ERPNext benches: uat on :8001 is shared with colleagues, test* benches are dev copies)'';
      neverWithoutAsking = [
        "anything on the uat bench or its database (migrate, restore, restart, data changes)"
        "database dumps/restores or direct SQL writes"
        "anything touching the pi-task worker pool (/srv/pi-envs, pi-task-control)"
      ];
    };

    agents.intro = ''
      # pi-admin: coding agent on devpro

      You run on **devpro**, a NixOS dev VM for professional work (mostly ERPNext /
      Frappe development), as user `cristian`. You work in the folder the user
      started you in, which can be any project: follow that project's own
      `AGENTS.md`/`CLAUDE.md` and work there.

      ## Where things live

      - ERPNext workspace: `/home/cristian/development/erpnext` (its own git repo and
        `AGENTS.md`); custom apps such as `domino_billing`, `domino_finance`,
        `dominobyte_roster` and `conturi` are separate git repos inside it. Benches are
        under `benches/` (`uat` on :8001 is used by colleagues; `test*` benches are dev
        copies).
      - Run every `bench` command inside the devshell:
        `nix develop ~/development/erpnext --command bench ...` (outside it the mariadb
        client, yarn and Node 24 are missing and bench fails with misleading errors).
      - devpro's config repo: `/home/cristian/development/nixos-devvms`. It is deployed
        from cdssrv02 (`nixos-rebuild switch --flake .#devpro --target-host devpro --sudo`),
        so here you only edit, build and commit; never run `nixos-rebuild` yourself.
      - `/srv/pi-envs/w1`…`w6` and `pi-task`/`pi-task-control` belong to the Pi worker
        pool that other agents run. Do not touch them.
      - pi-admin itself (gate, wrapper, shared AGENTS.md) is `cristids/pi-admin`;
        devpro's settings are in `modules/pi-admin/default.nix` of the config repo.
    '';
    agents.workRules = ''
      - **The uat bench is shared.** Do not migrate, restore, restart or change data on
        `uat` unless the user asks for that exact action. Prefer a `test*` bench for
        trying things.
      - Back up a site before any migrate or schema change you were asked to run.
      - When a change is finished (built and tested), commit it and push it if the
        repo has a remote. Stage only the files you changed; leave other uncommitted
        work alone and mention it.
      - Keep secrets out of output. Never print API keys, tokens or passwords
        (site_config.json, `/var/lib/erpnext-mcp/credentials.json`).
    '';
    agents.extraReadOnlyTools = ", plus ERPNext reads (list_environments, get_doctype_meta,\n  list_docs, get_doc, run_report); ERPNext writes (create_doc, update_doc,\n  call_method) ask";
    agents.extraUntrusted = [ "ERPNext document contents" ];
  };
}
