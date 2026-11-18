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
