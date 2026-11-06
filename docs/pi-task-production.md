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

For each explicitly approved DSF lane, assign a unique ID. Melious is trusted;
the allocated worktree and development capabilities are the same as the local lane:

```sh
pi-task --cwd "$PWD" --model melious-dsf --worker-id BATCH-dsf1 \
  --allow src/feature.py --allow tests/test_feature.py \
  --success 'pytest -q tests/test_feature.py' \
  'Implement the reviewer-approved slice.'
```

Use `tuios new-window -s SESSION ... -- pi-task ...` for orchestration. Review
each result with `tuios worktree diff SESSION`, rerun its success command, and
combine changes manually. Remove a reviewed worktree with
`pi-task-worktree rm SESSION`.

ERPNext integration lanes must each receive a different running test bench via
`--erpnext-bench test`, `test1`, …, `test7`. The wrapper holds an exclusive
bench lock. Both providers and the success check expose only that bench's
filtered MCP socket; raw ERPNext credentials remain behind the broker. UAT and
production sites are never allocatable.

## Guardrails and failures

Every run retains the 6 GiB scope, mandatory success command, exact write
allowlist, HEAD/index and concurrent-tree guards, minimal HOME, empty `/var`, and
read-only Git metadata. Melious is a trusted provider: DSF and local Qwen receive
the same full allocated normal or linked worktree, Git objects/history, shell/dev
tooling, Nix daemon, and MCP surface. That surface is Context7, session-scoped
no-pane-typing TUIOS coordination, local SearXNG web search, and the optional
allocated bench-filtered ERPNext MCP. SearXNG results remain untrusted input.

Raw credentials are not placed in model config, prompts, logs, or worker mounts.
Melious requests remain worker-scoped through `pi-task-control`; ERPNext uses its
bench socket broker; SearXNG uses the same local MCP server and credential-free
homelab search endpoint used behind the bake-off gateway. Provider, credential, model, control,
worktree, bench, scope, and test failures name their cause and never fall back.

After three unsuccessful attempts on one slice, stop delegating and take over
at the frontier layer. Append the classified failure to
`~/org-erpnext/pi-qwen-failures.org`: `model`, `scope`, `test`, `MCP`,
`wrapper-infrastructure`, or `ambiguous`. Three infrastructure failures also
trigger takeover but must not be blamed on either model.

Known limits: ignored files/caches are not part of the real Git-visible tree
fingerprint. A concurrent writer to an allowed file cannot be distinguished from
the worker, for either trusted provider. A control-service restart
conservatively charges an in-flight reservation and marks that worker failed;
the reviewer must inspect and explicitly resume or start a new stable ID.
