# Prepared and guided contender assessment

User requirement, 2026-11-18: compare what each contender can achieve under our
setup and guidance, not its defaults. This is a prerequisite for interpreting
operational results or recommending a migration.

## Preparation before scoring

For each pinned contender, inspect its version-specific implementation and
supported configuration. Enable and verify useful native memory, selective
retrieval, skills, context management, tools and review workflows. Supply the
same operational knowledge, privacy boundaries and infrastructure access while
using each harness's own supported mechanisms. Do not penalize a contender for
an adapter or configuration defect; repair that defect before scored runs.

Use a separate practice curriculum to coach the agent and validate its setup.
Teach NixOS administration, recovery, key handling, evidence-based procedures,
source trust and purpose checks for business-data access. Do not teach the
scored tickets' solutions or acceptance commands. Retain practice failures and
configuration changes as evidence of preparation effort.

A ready profile must identify:

- Exact harness version, configuration and enabled extensions/tools.
- Solver and reviewer models, provider routes and cost limits.
- Memory storage, retrieval/injection policy and workspace isolation.
- Native skills and common operational guidance supplied.
- Verified telemetry, sharing and network restrictions, including limitations.
- Practice tasks, outcomes, unresolved defects and supervisor interventions.
- Frozen configuration and a credential-free snapshot of reusable practice
  knowledge, with hashes so the scored starting state can be reproduced.

Do not claim a contender is prepared solely because its CLI launches. No
contender is ready until these checks have actual evidence. Freeze the prepared
profile before scoring. A later change creates a new profile and run; keep the
old results. Never snapshot credentials into the profile or learning artifacts.

## Guided execution and interpretation

Evaluate independent completion and supervised recovery separately. Guidance
may explain an operational intention or diagnose a problem, but record its
content, reason, timing and cost. Do not silently repair an agent's work and
credit it with independent success. Allow comparable opportunities for coaching
and recovery across contenders; report setup effort separately from task effort.

Measure successful runtime outcomes, safety, business-purpose review, private
memory use, token/context cost, procedure reuse, changed-prerequisite handling,
and the human effort needed to maintain the system. Learning must survive a new
agent process and improve later outcomes without carrying secrets or stale
assumptions into another workspace.

A common-model run helps diagnose harness differences. The deployment decision
must also consider each candidate's best supported configuration within our
approved providers and budget; an identical model is not the sole definition of
fairness. Record model differences explicitly. More model spending or more human
help must remain visible in the comparison.

Existing basic integration screens are development evidence only. They do not
establish a winner and must not be pooled into prepared-profile scores. Unknown
or unverified capabilities remain unknown, rather than being marked successful.

## Native practice implementation

The `practice` track is separate from tickets 1–62 and is never scored as part
of the comparison. It configures native skill discovery plus supported memory
and context settings through `../native_profiles.py`. It does not mark any
profile ready automatically.

```
harness-operations --root ~/bakeoff/assessment/practice-CANDIDATE-UNIQUE --track practice --candidates CANDIDATE --background
```

Practice 101 checks discovery of the installed operational skill and guest
inventory. Practice 102 teaches a service procedure; 103 starts a new agent
process against reset guests with a changed specification. Candidate home and
portable notes persist. Inspect actual native skill/memory tool use alongside
runtime results: success by reading portable notes alone does not prove native
retrieval. Retain practice traces and interventions separately from scored data.

Initial source-reviewed changes enable Hermes session search and Goose's local
memory extension, install common operational guidance in each supported native
skill directory, and set explicit supported context limits where the basic
adapter lacked them. OpenCode is configured using its pinned V1 schema. Network
isolation remains enforced outside the candidates by the runner. Runtime
verification, privacy checks, further coaching and frozen snapshots are still
required before scoring; source review alone is insufficient.

## Model selection and Paseo integration

Paseo is the target operator platform, including sessions, status, heartbeats and
review visibility. Herdr references in the supplied external research are context
only; do not build a Herdr integration for this assessment.

The user authorizes Melious and Claude API for other harnesses, while excluding
Claude Code and Codex as contenders. Select model roles for the workload, record
the configuration, and keep provider credentials outside candidate processes.
The current operations broker supports Melious; Claude requires a separately
validated credential-injecting route before use.

Use `--model glm-5.3 --review-model kimi-k3 --ticket-credits 3` for an explicit
strong-model trial. These options are recorded in the manifest. Per-ticket and
per-batch limits are operational guardrails, not the superseded EUR60 total cap.
Changing a model creates a new profile/run; do not overwrite earlier results.

## Upstream release policy

Always select the latest stable release published upstream in Git, then package
it in Nix. Neither Nix availability nor npm `latest` establishes stable status.
Record release URL, Git revision and actual binary version before the run.
Checked 2026-11-18: OMP18.22.2, Pi1.5.2, Hermes0.23.2, Vibe3.1.3,
OpenCode1.19.6 and Goose1.60.1. DSH has only GitHub prereleases and is deferred
from the stable series. Preserve previous runs; do not relabel their versions.

OMP uses its local memory backend and opt-in autolearn with managed skills. Its
profile restricts built-in tools and uses the same externally enforced private
network/broker boundary. `--offline` is defense in depth, not a firewall.
Validate actual lesson capture/retrieval before declaring this profile ready.

HarnessEvolve informs diagnosis, transferable lessons and regression checks; it
is not a prescribed architecture. Keep native learning and external coaching
separate, with fresh variants and permanent security regression checks.

Practice104 deliberately starts with empty portable notes while preserving the
native home. Its runtime grade alone does not certify native retrieval: inspect
the native tool results and source artifacts, not just the agent's self-report.
OMP initial practice recovered via portable notes after memory://root was not yet
available; creating a managed skill is not proof that it was retrieved later.

OMP local consolidation normally ignores sessions idle for less than 12 hours.
For the short practice curriculum, minRolloutIdleHours is explicitly zero and
stage1Concurrency is one. Record this coaching configuration; do not interpret
the previous missing memory artifact as absent native memory support. Letta's
--memfs flag requires remoteMemfs; the local backend must use its automatic
localMemfs path without that remote-only flag.

## Claude purpose-review bridge

`harness-claude-review` runs on the credential source host with a private Unix
socket and metadata-only ledger. Forward that socket through authenticated SSH
to a private devhobby socket; the candidate namespace never receives it.
`--purpose-review-model claude-sonnet-5-5 --claude-review-socket PATH` selects
this route for labctl purpose review only. Native harness reviewer/model roles
remain those selected by --review-model; this does not advertise Claude as a
Melious model or change the main solver.

The bridge accepts only a bounded, nonstreaming two-message Sonnet request,
without tools, arbitrary headers, endpoints or model choice. It rejects the
synthetic business-record marker, caps admitted requests per run and uses a
separate USD ceiling. The default USD10 ceiling is a local operational guard,
not the superseded assessment-wide EUR60 cap. Its ledger records admission,
status, token usage and estimated USD, never message text or credentials.

This first integration uses Anthropic's official OpenAI-compatible endpoint.
Prompt caching is unavailable and system messages are hoisted; do not represent
it as a fully tuned native-Claude solver configuration. Rates checked against
https://platform.claude.com/docs/en/models/overview on 2026-11-18: Sonnet5.5
USD2 input/USD10 output per million tokens. Report this estimate separately from
Melious credits and reconcile with provider billing.

### Reusing a prepared profile in a separate batch

Use `--prepared-from /absolute/path/to/completed-practice-run` when starting
security or operations checks after preparation. The launcher requires the same
candidate, a practice manifest and an inactive source driver. It copies the
native home and portable notes into the new run; it never shares writable state.
Symlinks and special files are rejected rather than followed. The source
manifest hash and every copied file hash are saved in `prepared-snapshot.json`
and included in the run manifest. Existing learned skill text is preserved when
reapplying the native configuration and coaching.

This is preparation provenance, not a claim that copied memories are trustworthy.
Poisoning and stale-memory challenges still require independent tests. Prior
security batches that started with fresh homes remain limited controls and do
not establish performance with the learned profile.
