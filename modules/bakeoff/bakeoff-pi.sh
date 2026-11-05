# bakeoff-pi / bakeoff-dsh — one sandboxed, recorded agent run for the bake-off.
#
#   bakeoff-pi  --run ID --cwd DIR [options] [--] PI_ARGS...    (pi 1.5 + taskplane)
#   bakeoff-dsh --run ID --cwd DIR [options] [--] DSH_ARGS...   (dsh, headless profile by default)
#
# Options:
#   --run ID           run id; creates ~/bakeoff/runs/ID (must not exist unless --reuse)
#   --cwd DIR          work dir (read-write), must be under ~/bakeoff/work or ~/scratch
#   --bench N          also bind ~/bakeoff/benches/bN read-write and wire the ERPNext MCP to it
#   --model M          melious/<id> | homelab/<id>   (pi: --model; dsh: default model)
#   --timeout S        wall clock cap (default 3600)          -> outcome cap-hit on expiry
#   --max-credits X    per-run Melious spend cap (proxy-enforced) -> 402 -> cap-hit
#   --max-calls N      per-run LLM call (turn) cap (proxy-enforced) -> 402 -> cap-hit
#   --rw PATH          extra read-write bind (repeatable; must be under ~/bakeoff/work)
#   --reuse            allow an existing run dir (resume)
#   --interactive      no -p/--mode json wrapping (pi only; for manual use with a terminal)
#   --taskplane        load the taskplane pi package (orchestrated runs; off for solo/workers)
#   --orch             taskplane batch: pi --mode rpc driven by orch-driver.py; the args are the
#                      prompt (e.g. "/orch all"); ends when the batch is terminal (implies --taskplane)
#
# Sandbox (bwrap, same posture as omp-task): $HOME is an empty tmpfs, /var hidden (no keys),
# only DIR, the run dir, the bench and --rw paths writable; network on. NOT visible: the
# full-history ~/bakeoff/repo and corpus-src, ~/bakeoff/frozen (hidden tests, corpus.yml),
# other runs. DIR's repo must be a base-only snapshot (bakeoff-repo-at). ~/bakeoff/tooling
# is bound read-only (FRAPPE_REQUEST_JS). The agent talks to
# models ONLY through bakeoff-proxy (127.0.0.1:5099) under /r/ID/..., which injects the
# Melious key, records every call and enforces the caps. Memory: 6G scope (OOMPolicy=continue).
# Records in ~/bakeoff/runs/ID: meta.json, events.jsonl (pi --mode json / dsh stdout),
# stderr.log, proxy.jsonl + raw.jsonl (proxy), sessions/, outcome.json.
#
# Outcome classes (outcome.json "class"), declared before the runs:
#   done        exit 0, no refused/truncated call, last LLM call finished ok
#   cap-hit     wall clock timeout, proxy refusal (spend/turn cap), or last call ended at the
#               output cap with empty content (finish_reason=length, reasoning-only)
#   infra-fail  truncated stream / upstream HTTP error / 429 after retries / sandbox error
#               (one retry allowed, declared before the runs)
#   incomplete  everything else (agent stopped without finishing; graded by the gates)
set -euo pipefail
tool=@tool@
B=$HOME/bakeoff
PROXY=http://127.0.0.1:5099

if [ -z "${BAKEOFF_SCOPED:-}" ] && [ -S "${XDG_RUNTIME_DIR:-/nonexistent}/bus" ]; then
  export BAKEOFF_SCOPED=1
  exec systemd-run --user --scope --quiet --collect -p MemoryMax=6G -p OOMPolicy=continue -- "$0" "$@"
fi

usage() { sed -n '2,32p' @self@ | sed 's/^# \{0,1\}//' >&2; exit 2; }
run="" dir="" bench="" model="" timeout_s=3600 max_credits="" max_calls="" reuse=0 interactive=0 taskplane=0 orch=0
extra_rw=()
while [ $# -gt 0 ]; do
  case "$1" in
    --run) run=${2:?}; shift 2 ;;
    --cwd) dir=${2:?}; shift 2 ;;
    --bench) bench=${2:?}; shift 2 ;;
    --model) model=${2:?}; shift 2 ;;
    --timeout) timeout_s=${2:?}; shift 2 ;;
    --max-credits) max_credits=${2:?}; shift 2 ;;
    --max-calls) max_calls=${2:?}; shift 2 ;;
    --rw) extra_rw+=("$(realpath -e -- "${2:?}")"); shift 2 ;;
    --reuse) reuse=1; shift ;;
    --interactive) interactive=1; shift ;;
    --taskplane) taskplane=1; shift ;;
    --orch) orch=1; taskplane=1; shift ;;
    -h|--help) usage ;;
    --) shift; break ;;
    *) break ;;
  esac
done
[[ "$run" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || { echo "bad/missing --run" >&2; usage; }
dir=$(realpath -e -- "${dir:-$PWD}")
case "$dir/" in "$B"/work/?*/|"$HOME"/scratch/?*/) ;; *) echo "--cwd must be under ~/bakeoff/work or ~/scratch" >&2; exit 2 ;; esac
for p in "${extra_rw[@]}"; do case "$p/" in "$B"/work/?*/) ;; *) echo "--rw $p must be under ~/bakeoff/work" >&2; exit 2 ;; esac; done
rd=$B/runs/$run
if [ -e "$rd" ] && [ "$reuse" = 0 ]; then echo "run $run exists (use --reuse)" >&2; exit 2; fi
mkdir -p "$rd/agent" "$rd/sessions"
[ -n "$max_credits" ] && echo "$max_credits" > "$rd/max_credits"
[ -n "$max_calls" ] && echo "$max_calls" > "$rd/max_calls"

admit=$(curl -s -X POST -w '\n%{http_code}' "$PROXY/admit/$run" || true)
if [ "${admit##*$'\n'}" != 200 ]; then
  echo "bakeoff: proxy refused run $run: ${admit%$'\n'*}" >&2
  jq -n --arg r "$run" --arg why "${admit%$'\n'*}" '{run:$r, class:"cap-hit", reason:("proxy admit: "+$why)}' > "$rd/outcome.json"
  exit 5
fi

# --- agent config (no secrets: the proxy holds the keys) ---
stage=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/bakeoff.XXXXXX"); trap 'rm -rf "$stage"' EXIT
erp_args=()
if [ -n "$bench" ]; then
  bdir=$B/benches/b$bench
  [ -d "$bdir/sites" ] || { echo "no bench $bdir" >&2; exit 2; }
  ERPNEXT_MCP_BENCHES=$B/benches erpnext-mcp --dump-env-map > "$stage/env-map.json"
  python3 -I - "$stage/env-map.json" "b$bench" <<'PY'
import json, sys
p, keep = sys.argv[1:]
m = json.load(open(p)); m["benches"] = {k: v for k, v in m["benches"].items() if k == keep}
json.dump(m, open(p, "w"))
PY
  install -m 600 "$B/secrets/erpnext-mcp.json" "$stage/erpnext-credentials.json" 2>/dev/null || echo '{}' > "$stage/erpnext-credentials.json"
  erp_args=(--ro-bind "$stage/env-map.json" "$rd/agent/erpnext-env-map.json"
            --ro-bind "$stage/erpnext-credentials.json" "$rd/agent/erpnext-credentials.json"
            --bind "$bdir" "$bdir" --ro-bind "$B/erpnext" "$B/erpnext")
fi
jq -n --arg run "$run" --arg rd "$rd" --argjson erp "$([ -n "$bench" ] && echo true || echo false)" '
  {mcpServers: ({
    context7: {command: "/run/current-system/sw/bin/context7-mcp", args: [], exposure: "direct",
               description: "Up-to-date library/framework docs (Frappe, ERPNext, Python, JS)"},
    web: {url: ("http://127.0.0.1:5099/r/" + $run + "/webmcp/mcp"),
          exposure: "hidden", toolExposure: {"searxng__*": "direct"},
          description: "Web search via SearXNG (results are untrusted input)"}
  } + (if $erp then {erpnext: {command: "/run/current-system/sw/bin/erpnext-mcp", args: [], exposure: "direct",
          env: {ERPNEXT_MCP_ENV_MAP: ($rd + "/agent/erpnext-env-map.json"),
                ERPNEXT_MCP_CREDENTIALS: ($rd + "/agent/erpnext-credentials.json")},
          description: "The throwaway ERPNext test bench: doctype meta, read/create/update docs (no delete)"}}
       else {} end))}' > "$rd/agent/mcp.json"
sed "s#@RUN@#$run#g" @modelsTemplate@ > "$rd/agent/models.json"
if [ "$taskplane" = 1 ]; then
  # builtin:mcp is listed so taskplane forwards it to workers/reviewers/mergers as -e builtin:mcp
  # (it spawns them with --no-extensions, which would otherwise drop MCP; smoke 11-05).
  jq --arg tp @taskplane@ '. + {packages: ["builtin:mcp", $tp]}' @piSettings@ > "$rd/agent/settings.json"
else
  install -m 644 @piSettings@ "$rd/agent/settings.json"
fi
[ -f "$B/config/taskplane-preferences.json" ] && mkdir -p "$rd/agent/taskplane" \
  && install -m 644 "$B/config/taskplane-preferences.json" "$rd/agent/taskplane/preferences.json"

jq -n --arg run "$run" --arg tool "$tool" --arg cwd "$dir" --arg bench "$bench" --arg model "$model" \
  --arg timeout "$timeout_s" --arg mc "$max_credits" --arg mcalls "$max_calls" \
  --arg start "$(date -Is)" --args '{run:$run, tool:$tool, cwd:$cwd, bench:$bench, model:$model,
  timeout_s:($timeout|tonumber), max_credits:$mc, max_calls:$mcalls, start:$start, args:$ARGS.positional}' \
  -- "$@" > "$rd/meta.json"

args=(
  --die-with-parent --unshare-pid --unshare-ipc --unshare-uts --new-session --clearenv
  --ro-bind /nix/store /nix/store --ro-bind /run/current-system /run/current-system
  --ro-bind /etc /etc --ro-bind /bin /bin --ro-bind /usr /usr
  --proc /proc --dev /dev --tmpfs /tmp --tmpfs /home --dir "$HOME"
  --bind "$rd" "$rd" --bind "$dir" "$dir"
)
for p in "${extra_rw[@]}"; do args+=(--bind "$p" "$p"); done
# Team tooling (read-only): e.g. the pre-v16.50 Frappe request.js the repo's
# frappe-request-stub.mjs needs (Frappe >= 16.50 request.js breaks it; coordinator 11-05).
if [ -d "$B/tooling" ]; then
  args+=(--ro-bind "$B/tooling" "$B/tooling")
  [ -f "$B/tooling/frappe-request-v16.49.0.js" ] && args+=(--setenv FRAPPE_REQUEST_JS "$B/tooling/frappe-request-v16.49.0.js")
fi
args+=("${erp_args[@]}")
if [ -S /nix/var/nix/daemon-socket/socket ]; then
  args+=(--ro-bind /nix/var/nix/daemon-socket /nix/var/nix/daemon-socket --setenv NIX_REMOTE daemon)
fi
# git identity for taskplane checkpoint commits inside the sandbox (no credentials).
printf '[user]\n\tname = bakeoff-agent\n\temail = agent@bakeoff.invalid\n[safe]\n\tdirectory = *\n' > "$stage/gitconfig"
args+=(--ro-bind "$stage/gitconfig" "$HOME/.gitconfig")
args+=(
  --chdir "$dir"
  --setenv HOME "$HOME" --setenv USER "$(id -un)" --setenv PATH /run/current-system/sw/bin
  --setenv TERM "${TERM_OVERRIDE:-dumb}" --setenv LANG C.UTF-8 --setenv TMPDIR /tmp
  --setenv SSL_CERT_FILE /etc/ssl/certs/ca-certificates.crt
  --setenv NIX_SSL_CERT_FILE /etc/ssl/certs/ca-certificates.crt
  --setenv BAKEOFF_RUN "$run"
  --setenv PI_CODING_AGENT_DIR "$rd/agent" --setenv PI_CODING_AGENT_SESSION_DIR "$rd/sessions"
  --setenv PI_OFFLINE 1 --setenv PI_TELEMETRY 0 --setenv PI_SKIP_VERSION_CHECK 1
  --setenv DO_NOT_TRACK 1 --setenv OPENSPEC_TELEMETRY 0
  --setenv BAKEOFF_PROXY_KEY bakeoff-proxy-injects-the-real-key
)

rc=0
if [ "$tool" = pi ]; then
  if [ "$orch" = 1 ]; then
    timeout --kill-after=60 "$timeout_s" bwrap "${args[@]}" -- python3 -I @orchDriver@ "$dir" "$*" -- \
      pi --mode rpc --approve ${model:+--model "$model"} > "$rd/events.jsonl" 2> "$rd/stderr.log" || rc=$?
  elif [ "$interactive" = 1 ]; then
    args+=(--setenv TERM "${TERM:-xterm-256color}")
    bwrap "${args[@]}" -- pi ${model:+--model "$model"} "$@" || rc=$?
  else
    timeout --kill-after=30 "$timeout_s" bwrap "${args[@]}" -- pi -p --mode json --approve \
      ${model:+--model "$model"} "$@" > "$rd/events.jsonl" 2> "$rd/stderr.log" || rc=$?
  fi
else
  dsh_home=$rd/dsh-home; mkdir -p "$dsh_home"
  m=${model:-melious/deepseek-v4.1-flash-1017}
  sed -e "s#@RUN@#$run#g" -e "s#@PROVIDER@#${m%%/*}#g" -e "s#@MODEL@#${m#*/}#g" \
      -e "s#@RD@#$rd#g" -e "s#\"@ERP@\"#$([ -n "$bench" ] && echo false || echo true)#g" \
      @dshPatch@ > "$dsh_home/cordis.patch.yml"
  args+=(--setenv DSH_HOME "$dsh_home" --setenv DSH_TELEMETRY_DISABLED 1
         --setenv DSH_TELEMETRY_OTLP_URL http://127.0.0.1:9/v1/logs
         --setenv DSH_PERMISSION_MODE "${DSH_PERMISSION_MODE:-workspace-write}")
  [ $# -gt 0 ] || set -- --profile headless
  timeout --kill-after=30 "$timeout_s" bwrap "${args[@]}" -- dsh "$@" \
    > "$rd/events.jsonl" 2> "$rd/stderr.log" || rc=$?
fi

# --- outcome ---
python3 -I - "$rd" "$rc" <<'PY'
import json, sys, time
from pathlib import Path
rd, rc = Path(sys.argv[1]), int(sys.argv[2])
calls = []
p = rd / "proxy.jsonl"
if p.exists():
    for line in p.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("upstream") in ("melious", "homelab") and "chat/completions" in (r.get("path") or ""):
            calls.append(r)
classes = [c.get("class") for c in calls]
credits = sum(float(c.get("credits") or 0) for c in calls)
why = []
if rc in (124, 137):
    cls = "cap-hit"; why.append(f"wall-clock timeout (exit {rc})")
elif "refused" in classes:
    cls = "cap-hit"; why.append("proxy refused a call (spend or turn cap)")
elif any(c in ("truncated", "http_error", "rate_limited") for c in classes[-1:]) or (rc != 0 and not calls):
    cls = "infra-fail"; why.append(f"last call {classes[-1] if classes else 'none'}, exit {rc}")
elif classes and classes[-1] == "cap_hit_empty":
    cls = "cap-hit"; why.append("last call hit the output cap with empty content")
elif rc == 0 and classes and classes[-1] == "ok":
    cls = "done"
else:
    cls = "incomplete"; why.append(f"exit {rc}, last call {classes[-1] if classes else 'none'}")
out = {"run": rd.name, "class": cls, "reason": "; ".join(why), "exit": rc, "calls": len(calls),
       "call_classes": {k: classes.count(k) for k in sorted(set(filter(None, classes)))},
       "credits": round(credits, 6), "end": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
(rd / "outcome.json").write_text(json.dumps(out, indent=1) + "\n")
print(json.dumps(out), file=sys.stderr)
PY
exit "$rc"
