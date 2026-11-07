# Production Pi worker pool on devpro

`pi-task` runs one substantial OpenSpec implementation packet. A reviewer owns the
brief, file scope, worktree, test command, provider choice, bench allocation,
diff review, and final commit. Workers never commit.

## Models and live controls

Image input is declared for local Qwen, DSF1017, GLM5.3Flash, KimiK3 and KimiK2.7Code.
GLM5.3 and Qwen3.8Max remain text-only. Pi's read tool can attach PNG screenshots
for the image-capable models. Local Qwen has a reviewer-reported image probe;
Melious declarations follow its2026-11-07 catalog, not a completed visual-quality
evaluation. Existing workers keep their per-run config snapshot until a new launch
or explicit resume. Screenshot review remains part of reviewer acceptance.

Only tested coding models are exposed. `local-qwen` remains the default
(`homelab/strata/qwen3.8-flash`). External selections are `melious-dsf`
(`deepseek-v4.1-flash-1017`), `melious-glm-5.3`, `melious-glm-5.3-flash`,
`melious-kimi-k3`, `melious-kimi-k2.7-code`, and `melious-qwen3.8-max`.
All external models share the same cap and worker pool, not separate budgets.
Each external launch requires `--model ALIAS --worker-id UNIQUE_ID`.
The proxy pins that worker to its selected model; its Pi config exposes only
that model, without fallback. Existing DSF IDs/callers remain compatible.
Every delegated batch has
exactly one local-Qwen lane. The standing authorization is that lane plus zero
to five external lanes within the live 200-credit cap; an ordinary batch inside those
bounds does not need fresh approval. Changing cap or worker limit remains a
governance action taken only when Cristian directs it.

Local Qwen has one global exclusive lane. Melious uses two independent persistent
controls: `monthly_cap` starts at 200 credits and `worker_limit` starts at 5.
Neither value is compiled as a maximum: the reviewer can set the limit to zero
or any nonnegative integer and can raise or lower the cap. Changes survive
service restarts. A worker blocked by the budget, limit, or a manual pause waits
without spending; its Pi process and worktree remain alive. In-flight requests
may finish when a control is lowered.

Every external lane needs a stable reviewer-assigned `--worker-id`. Each provider
request uses a worker-scoped loopback URL and passes through `pi-task-control`,
which reserves credit atomically before forwarding it and settles against the
provider-reported charge. Missing usage is charged conservatively. State rolls
over on Europe/Bucharest calendar months and stale reservations are recovered
conservatively after restart. Audit records contain control/request metadata,
actor/session/pane when available, reason, and old/new state—never prompts,
source, responses, credentials, or secrets.

Model-specific reservations use declared context, the configured 65536 output
token ceiling, and catalog token prices (2026-11-07). These are estimates, not a
guaranteed provider-price ceiling: routing prices may differ. Actual billing is
settled even if it exceeds the reservation. Pi display prices are estimates too.

Output cap: 65536 is the configured fallback, not a verified provider maximum:
Melious returned null maximum output values on 2026-11-07. Context windows stay
unchanged. A terminal `length` response logs `length-stop` with model/worker/turn,
prints a cut-off warning and fails the run with exit7 even when Pi exits0. It skips
acceptance and finalizes the worker as failed; TUIOS shows output-cap failure.
Pi receives closed stdin so background launchers cannot leave it waiting for input.

An otherwise successful Pi exit with no final assistant message, no final text/tool
call (thinking alone is not a final answer), or missing final input/output usage is
an infrastructure-reply failure: exit8, `invalid-final-reply` audit row, failed
controller registration, errored pane, and no acceptance execution. Reported zero
usage is valid. This does not refund or change conservative missing-billing charges;
it prevents incomplete runs from appearing successful.

### Resume an interrupted conversation

New runs print a stable run ID. Use `pi-task --resume RUN_ID` without other options
inside a TUIOS pane. It restores the original packet/model/attempt/bench/scope and
the same Pi conversation, with a new billable controller registration linked by the
run ID. This is not a new attempt and does not reset spending or bypass live controls.
Original Git/source scope, other apps, app links and acceptance hashes are rechecked;
post-interruption checkout changes refuse resume. The model must inspect current
files/tests before repeating actions: conversation resume is not transaction rollback.

Private records live in `~/.local/state/pi-task/runs/RUN_ID` (0700 directory,0600
manifest). Only that run's session directory is mounted at `/pi-session`; other runs
and the host home remain hidden. These retained sessions contain task/tool content,
unlike metadata-only audit logs. No automatic deletion is performed.
Completed runs, acceptance/policy failures and explicitly stopped workers are refused.
Nonzero Pi exits (including truncation/empty replies) can resume after scope validation.
After a hard kill, a stale live controller registration must first be reconciled by
the reviewer; do not finish a registration while its worker is still running.
Historical `--no-session` runs cannot be resumed. Nothing auto-retries.

Read status at any time:

```sh
pi-task-control status
```

It reports cap, spent, reserved, remaining, limit, and worker IDs grouped as
active, in-flight, budget-waiting, limit-waiting, manual-paused, stopped,
completed, or failed. Every mutation requires a nonempty reason and is a
governance action: use these commands only after explicit Cristian instruction.

```sh
pi-task-control set-cap 250 --reason 'Cristian approved this batch budget'
pi-task-control set-worker-limit 8 --reason 'Cristian approved eight DSF lanes'
pi-task-control pause worker-2 worker-4 --reason 'review requested'
pi-task-control resume worker-2 --reason 'review complete'
pi-task-control stop --count 2 --reason 'cancel oldest two live workers'
pi-task-control stop --all --reason 'cancel the DSF pool'
```

Selectors accept explicit IDs, `--count N` (oldest nonterminal workers), or
`--all`. Raising the cap or limit alone never resumes or newly admits waiting
workers; select them explicitly with `resume`. Stopping wakes blocked requests
with a controlled failure. There is no provider fallback, OMP, best-of-N, or
automatic retry.

## Reviewer workflow

### Advisory Clef failure triage

Reviewers may run `pi-task-control triage --summary-file /path/to/summary.txt`
(or `--summary-file -` for stdin). Supply a concise objective, observed failure,
relevant error excerpts and evidence; remove credentials/secrets first. Cristian
explicitly authorized sending these reviewer-prepared summaries to trusted Melious.
No automatic log collection or worker invocation is enabled.

Clef returns advisory probabilities for infrastructure, implementation, requirements,
or reviewer investigation. It never approves code, retries, stops or resumes workers.
Reviewers remain responsible; probabilities are not validated accuracy estimates.
Unavailable/full/paused/over-budget service returns skipped/unavailable advice without
blocking worker progress. Each call has a20-second network timeout and a12000-byte
request limit. Requests share the external pool and200-credit budget. Decision API
responses lack actual billing cost, so the ledger conservatively charges the0.004
reservation per attempted call (not a claim of the exact provider invoice).
Summary contents are not persisted by the controller; only normal metadata/accounting.
Use deterministic failure reasons such as output-cap directly; Clef is for ambiguity.

Create a separate TUIOS worktree session for every worker; never use a parallel
shell launcher or share a writable checkout:

```sh
pi-task-worktree new pi/BATCH-qwen --repo ~/development/PROJECT --detach
pi-task-worktree new pi/BATCH-dsf1 --repo ~/development/PROJECT --detach
```

Prepare one substantial, self-contained OpenSpec work packet. Include the
objective, relevant context/spec references, exact writable files, acceptance
criteria and command, dependencies, allocated worktree/bench, and expected
report. The worker autonomously inspects, implements, runs the command, diagnoses
failures, and iterates test/fix within that scope. The wrapper independently
runs the same command once more after Pi exits.

Template:

```text
Objective: <complete bounded outcome>
Context/spec: <OpenSpec and relevant project references>
Allowed scope: <what may change; --allow remains authoritative>
Acceptance criteria: <observable behavior>
Required tests: <the exact --success command>
Dependencies/allocation: <worktree, bench, related services>
Report: <diff, tests, blocker details if any>
```

Run exactly one local lane:

```sh
pi-task --cwd "$PWD" --model local-qwen \
  --attempt 1 \
  --allow src/feature.py --allow tests/test_feature.py \
  --success 'pytest -q tests/test_feature.py' \
  'Objective: implement the complete approved OpenSpec packet. ...'
```

For each DSF lane within the standing authorization, assign a unique stable ID.
Melious is trusted; the allocated worktree and development capabilities are the
same as the local lane:

```sh
pi-task --cwd "$PWD" --model melious-dsf --worker-id BATCH-dsf1 \
  --attempt 1 \
  --allow src/feature.py --allow tests/test_feature.py \
  --success 'pytest -q tests/test_feature.py' \
  'Objective: implement the complete approved OpenSpec packet. ...'
```

Use `tuios new-window -s SESSION ... -- pi-task ...` for orchestration. Review
each result with `tuios worktree diff SESSION`, rerun its success command, and
combine changes manually. Remove a reviewed worktree with
`pi-task-worktree rm SESSION`.

### ERPNext: complete W1–W6 development environments

Each ERPNext worker gets one complete disposable bench `/srv/pi-envs/w1` through
`w6`, its own database/user, app clones, Python environments, Node/yarn/build
toolchain, Redis sockets and HTTP server. Shared `test`, `test1`…`test7`, UAT and
production benches cannot be allocated to a new Pi packet. Existing sessions
are unaffected. `/home`, other W slots and `/srv/pi-envs/.control` stay hidden.

The reviewer prepares one slot before launching the packet. Inspect status and
collect existing evidence before any intentional reset; the runner never resets
a slot automatically. `benches/pi-envs.md` in the ERPNext repo is the authoritative
provision/reset/link procedure. A prepared app may be used directly, or linked
to a standalone packet clone **inside the same W slot**. Never link its Git
metadata or installed Python imports back into hidden `/home`.

```sh
manager=/home/cristian/development/erpnext/benches/pienv.sh
$manager status 1
# If necessary, prepare/link the reviewed app baseline using pi-envs.md first.
$manager start 1
$manager web 1
# Launch inside the assigned TUIOS pane:
pi-task --cwd /srv/pi-envs/w1/apps/dominobyte_roster \
  --erpnext-bench w1 --allow-app \
  --success 'cd "$E2E_BENCH" && env/bin/python config/pienv_run_tests.py test_annual_summary_native' \
  'Complete the reviewed OpenSpec plan; run native tests and repair findings.'
```

Use the same command with `--model melious-dsf --worker-id BATCH-dsf1` and a
different slot for DSF. `--allow-app` permits existing and new source files in
the selected disposable app, with final diff review. For a narrowly scoped fix,
repeat exact `--allow FILE` instead. Other apps' source and every app's Git
metadata remain guarded; runtime DB/log/cache/build/environment effects are
allowed. Workers never commit, so untracked files must also be reviewed and
exported by the reviewer before reset.

The runner holds the same per-slot lease used by the lifecycle manager from
readiness checks through all implementation and acceptance testing. Stop/reset/
link cannot invalidate a running mount. Toolchain variables are inherited by
Pi, child tools and the independent success command: `E2E_BENCH`, `E2E_SITE`,
`PI_BASE_SHA`, Python/dev PATH and library paths. Workers can run `bench --site
"$E2E_SITE" migrate`, `bench build`, native test adapters, `pi-env-runtime reload`
and foreground `pi-env-runtime worker` when a task needs queue processing.

Browser tests use Nix-managed Chromium and Playwright. `E2E_BASE_URL` is always
set to the assigned W URL (preventing legacy tests' shared-bench defaults), and
`E2E_CHROMIUM` selects the system browser. Node can resolve `@playwright/test`
through `NODE_PATH`; run `playwright test` for the existing `qa/e2e` stack.
Run Python browser scripts with `pi-browser-python`, a dedicated Nix interpreter
with Playwright and requests, without altering the Frappe virtualenv. Browser
profiles/cache are disposable inside the sandbox; save evidence to the W `logs`
directory. The private `/login` page was loaded headlessly, DOM checked and
screenshotted with `/home` hidden. This verifies tooling, not every application UI.

ERPNext MCP runs **inside the worker sandbox**, with a read-only generated map
containing only the assigned `wN`/`wN.localhost` and that slot's own API identity.
It reads the current credentials after web startup; reset rotates them. No shared
ERPNext credentials or old bench aliases are mounted. Web reload picks up changed
packet code; check the imported app path and test the actual behavior.

Only start the services a packet needs. The VM has 32 GiB until its next full
shutdown/start (64 GiB configured); six simultaneous heavy suites are not a
capacity guarantee. Model cap/limit, 6 GiB worker scope and live monitoring remain.

SearXNG is optional. An outage is reported as degraded and does not block
unrelated coding. Add `--require-web` only when web access is an acceptance
dependency; then admission fails clearly if SearXNG is unavailable.

## Live monitoring and audit

The supervisor reports its own TUIOS pane as Pi: `working` through admission,
implementation and independent acceptance checks, `done` only after all checks
pass, otherwise `errored`. Reports use the inherited pane identity/token outside
the worker sandbox and are best-effort with a two-second timeout. Pi's installed
upstream extension intentionally ignores JSON/print mode, so it cannot provide
this lifecycle. No additional socket directory is exposed. A forced kill can
prevent the final report; consult process/controller state as well. Already
running workers keep their old supervisor until the next packet.

Both providers use one execution and telemetry path. Pi JSON is consumed while
the process runs: tool start/end and per-turn model/token/cost usage are printed
live and appended as sanitized JSONL to
`~/.local/state/pi-task/audit.jsonl`. The log records run/worker/provider/model,
timestamps, tool names, result status, normalized network destinations, and
usage only. It never records briefs/prompts, source, command arguments, tool
payloads/results, credentials, URL paths, or query strings. Writes are locked
across workers; at 10 MiB the file rotates to one `audit.jsonl.1` backup,
bounding retained telemetry near 20 MiB.

Observe a live worker through its TUIOS pane, or follow durable metadata:

```sh
tuios capture-pane -s SESSION -w WORKER --scrollback --lines 80
tail -F ~/.local/state/pi-task/audit.jsonl | jq -c .
```

Network monitoring traces `connect(2)` for Pi and every child process in the
worker sandbox. It records IP address/port plus a public/private/known-service
category, or only a category for Unix sockets. This covers normal TCP and
connected-UDP clients, including shell tools and MCP subprocesses, without a
domain allowlist. It does not see payloads, URL/hostname details, unconnected
datagrams, or traffic performed later by another service. In particular, a Nix
download delegated through the daemon is visible only as a Nix-daemon socket
connection, and DSF is visible as its localhost control relay; the relay's
declarative upstream is fixed to `https://api.melious.ai` and its controller
audit records reservation/settlement. These are explicit attribution limits,
not claims of full packet capture.

## Guardrails and failures

Worker scopes set `MemoryMax=6G` and `MemorySwapMax=0`. Existing processes keep
their launch-time limits; this does not restart or change an active worker.

Reviewer-owned acceptance files are copied into private per-run storage and
mounted read-only at their original paths in BOTH the worker and final-check
sandboxes. Defaults: W `config/*.py`, `config/*acceptance*.json`, `toolchain.sh`,
and every existing OpenSpec packet file (including briefs and baseline manifests).
The OpenSpec directory itself is read-only. W `config/` is also read-only except
its existing `pids/`, `web-reload` and `bench_build.lock` runtime paths. Host-written
attestations/approvals there remain live across atomic replacement, including
`root-runtime-attestation.json` created after admission; they are not frozen copies.
Host-only approval files outside W remain hidden. The reviewer must validate the
trusted baseline before launch, and keep attestation inputs outside worker scope.
Declare any additional checker, fixture or expected-result dependency with
repeatable `--acceptance-file PATH` (relative to cwd or absolute within own W).
Paths must be existing canonical regular files, not symlinks; explicit `--allow`
overlap is refused, and `--allow-app` does not override protected mounts.
The originals are compared with the private copies before and after final checks,
so a writable hard-link alias or renamed parent cannot silently change the gate.
Do not include worker-authored tests in this protected set when developing tests
is the assignment. Those tests are still subject to independent reviewer approval.
This preserves check definitions; it is not a claim that arbitrary candidate code
running in the same interpreter cannot affect a test harness. Independent review
and authoritative expected outcomes remain necessary.

Every run retains the 6 GiB scope, mandatory success command, reviewed source
scope (exact files or one disposable W app), HEAD/index and concurrent-tree guards,
minimal HOME, empty `/var`, and
read-only Git metadata. Melious is a trusted provider: DSF and local Qwen receive
the same full allocated normal or linked worktree, Git objects/history, shell/dev
tooling, Nix daemon, and MCP surface. That surface is Context7, session-scoped
no-pane-typing TUIOS coordination, local SearXNG web search, and the optional
allocated bench-filtered ERPNext MCP. SearXNG results remain untrusted input.

Provider/shared credentials are not placed in model config, prompts, logs, or
worker mounts. A W worker has only its own disposable database/API credentials.
Melious requests remain worker-scoped through `pi-task-control`; W ERPNext MCP
runs in the allocated sandbox; SearXNG uses the same local MCP server and credential-free
homelab search endpoint used behind the bake-off gateway. Provider, credential, model, control,
worktree, bench, scope, and test failures name their cause and never fall back.

Number retries with `--attempt 1`, `2`, then `3`; there is no automatic retry.
Each failure prints and audits a bounded classification and attempt 3 explicitly
marks takeover required. After three unsuccessful attempts on one slice, stop
delegating and take over at the frontier layer. Append the classified failure to
`~/org-erpnext/pi-qwen-failures.org`: `model`, `scope`, `test`, `MCP`,
`wrapper-infrastructure`, or `ambiguous`. Three infrastructure failures also
trigger takeover but must not be blamed on either model.

Known limits: ignored files/caches are not part of the real Git-visible tree
fingerprint. A concurrent writer to an allowed file cannot be distinguished from
the worker, for either trusted provider. A control-service restart
conservatively charges an in-flight reservation and marks that worker failed;
the reviewer must inspect and explicitly resume or start a new stable ID.
