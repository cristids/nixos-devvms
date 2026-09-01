{ pkgs, ... }:

# devhobby — hobby projects / agent playground. Carries only its own Melious key and
# hobby-scoped tokens; the freedom to let agents run loose here is the point of
# keeping it separate from devpro. Everything else: ../common.
{
  networking.hostName = "devhobby";

  # Hobby languages (not in devpro on purpose): Common Lisp + Prolog. Emacs-side
  # tooling (sly/slime, ediprolog…) lives in the personal emacs config, not here.
  environment.systemPackages = with pkgs; [
    sbcl
    swi-prolog

    # tramp-rpc server (2026-08-30) — laptop Emacs edits this VM over
    # /rpc:devhobby: instead of /ssh: (MessagePack-RPC, no shell parsing).
    # Lands at /run/current-system/sw/bin/tramp-rpc-server; the laptop-side
    # client finds it via PATH, so no auto-deploy into ~/.cache (which VM
    # reprovisions would wipe). Client package: nixos-laptops, same flake
    # input.
    emacs-tramp-rpc-server

    # Harness web-UI toolchain (2026-08-31, ~/agentic-dev-team PLAN.org D-UI-3):
    # tailwindcss_4 = the standalone CLI (no node), matches the stack ADR.
    # playwright-driver browsers land via the env vars below; projects pin
    # pip playwright to the 1.56.x line (driver here is 1.56.1; PyPI ships
    # 1.56.0 — same browser revisions, compatible).
    tailwindcss_4
  ];

  # nix-ld: pip wheels with C/C++ extensions (greenlet, uvloop, pydantic-core…)
  # expect a conventional dynamic linker; without this every second wheel dies
  # with "libstdc++.so.6: cannot open shared object file" (litellm and
  # playwright both hit it, 2026-08-31). This is the durable fix; per-process
  # LD_LIBRARY_PATH wrappers are the workaround it replaces.
  programs.nix-ld = {
    enable = true;
    libraries = with pkgs; [ stdenv.cc.cc.lib zlib openssl ];
  };

  # Playwright on NixOS: browsers come from nixpkgs (patched ELF), NOT from
  # `playwright install` (its downloads are dynamically linked and break here).
  # The env vars point the pip-installed client at the nix-provided browsers.
  environment.variables = {
    PLAYWRIGHT_BROWSERS_PATH = "${pkgs.playwright-driver.browsers}";
    PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS = "true";
  };
}
