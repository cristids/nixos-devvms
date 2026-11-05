# Production Pi worker pool on devpro

`pi-task` runs one bounded OpenSpec implementation slice. A reviewer owns the
brief, file scope, worktree, test command, provider choice, bench allocation,
diff review, and final commit. Workers never commit.

## Models and live controls

Only `local-qwen` (the default, exact model
`homelab/strata/qwen3.8-flash`) and `melious-dsf` (exact model
`melious/deepseek-v4.1-flash-1017`) are exposed. Every delegated batch has
exactly one local-Qwen lane. Reviewers may add DSF lanes only after explicit
Cristian instruction.

Local Qwen has one global exclusive lane. DSF uses two independent persistent
controls: `monthly_cap` starts at 200 credits and `worker_limit` starts at 5.
Neither value is compiled as a maximum: the reviewer can set the limit to zero
or any nonnegative integer and can raise or lower the cap. Changes survive
service restarts. A worker blocked by the budget, limit, or a manual pause waits
without spending; its Pi process and worktree remain alive. In-flight requests
may finish when a control is lowered.

Every DSF lane needs a stable reviewer-assigned `--worker-id`. Each provider
request uses a worker-scoped loopback URL and passes through `pi-task-control`,
which reserves credit atomically before forwarding it and settles against the
provider-reported charge. Missing usage is charged conservatively. State rolls
over on Europe/Bucharest calendar months and stale reservations are recovered
conservatively after restart. Audit records contain control/request metadata,
actor/session/pane when available, reason, and old/new state—never prompts,
source, responses, credentials, or secrets.

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

Create a separate TUIOS worktree session for every worker; never use a parallel
shell launcher or share a writable checkout:

```sh
pi-task-worktree new pi/BATCH-qwen --repo ~/development/PROJECT --detach
pi-task-worktree new pi/BATCH-dsf1 --repo ~/development/PROJECT --detach
```

Run exactly one local lane:

```sh
pi-task --cwd "$PWD" --model local-qwen \
  --allow src/feature.py --allow tests/test_feature.py \
  --success 'pytest -q tests/test_feature.py' \
  'Implement the approved OpenSpec slice.'
```

For each explicitly approved external lane, assign a unique ID and expose only
the files needed for that slice:

```sh
pi-task --cwd "$PWD" --model melious-dsf --worker-id BATCH-dsf1 \
  --read src/contracts.py --read tests/fixtures \
  --allow src/feature.py --allow tests/test_feature.py \
  --success 'pytest -q tests/test_feature.py' \
  'Implement the reviewer-approved slice using only the exposed paths.'
```

Use `tuios new-window -s SESSION ... -- pi-task ...` for orchestration. Review
each result with `tuios worktree diff SESSION`, rerun its success command, and
combine changes manually. Remove a reviewed worktree with
`pi-task-worktree rm SESSION`.

ERPNext integration lanes must each receive a different running test bench via
`--erpnext-bench test`, `test1`, …, `test7`. The wrapper holds an exclusive
bench lock and exposes only that bench's filtered MCP socket. UAT and production
sites are never allocatable.

## Guardrails and failures

Every run retains the 6 GiB scope, mandatory success command, exact write
allowlist, HEAD/index and concurrent-tree guards, minimal HOME, empty `/var`, Context7, optional
bench-scoped ERPNext, and session-scoped read-only TUIOS MCP access. Provider,
credential, model, control, worktree, bench, scope, and test failures name their
cause and never fall back.

DSF sees a staged view containing only `--allow` plus reviewer-approved `--read`
paths and no original `.git`, common gitdir, objects, refs, config, alternates,
linked-worktree metadata, or history. Nested `.git` directories and symlinks are
refused while staging. Local Qwen may see the original normal-clone or linked-
worktree Git metadata read-only. The success command runs locally with read-only
original Git only after external inference ends. Never approve production
credentials, production data, secrets, or unrelated repository content in
external paths, the brief, or test output.

After three unsuccessful attempts on one slice, stop delegating and take over
at the frontier layer. Append the classified failure to
`~/org-erpnext/pi-qwen-failures.org`: `model`, `scope`, `test`, `MCP`,
`wrapper-infrastructure`, or `ambiguous`. Three infrastructure failures also
trigger takeover but must not be blamed on either model.

Known limits: ignored files/caches are not part of the real Git-visible tree
fingerprint. A concurrent writer to an allowed local-Qwen file cannot be
distinguished from the worker; DSF staging avoids that ambiguity by refusing
real-worktree movement before applying outputs. A control-service restart
conservatively charges an in-flight reservation and marks that worker failed;
the reviewer must inspect and explicitly resume or start a new stable ID.
