# Real NixOS operations assessment

Authorized 2026-11-18: actual infrastructure and coding, not a simulated demo.
The initial battery contains 50 operational tickets across Nix packaging and
modules, Python services, PostgreSQL, Podman, SSH credentials, TLS, security
review, recovery, memory reuse and integrated deployment. See `catalog.py` for
exact tasks and independent acceptance commands. The additional `cyber` track has
12 live networking/security tickets (51–62), bringing coverage to 62 distinct
tickets. Current primary sources and applicability distinctions are in SOURCES.md.

Three actual NixOS QEMU/KVM guests (admin/app/data) run inside devhobby. They have
real systemd, PostgreSQL, Podman, writable Nix stores and disk state. A private
VLAN connects them; QEMU external networking is restricted. The host user home,
production network and inference credentials are not available to them. Agents
administer the guests through `labctl exec NODE COMMAND`, with root inside these
guests. This transport is not a simulation and executes real shell commands.

Agent processes retain the earlier private network/filesystem namespace. Only the
model broker and guest administration API are reachable. Fresh candidate homes
are created per batch. Each ticket starts a new agent process, retaining native
memory and portable notes but resetting remote task state. This permits repeat
and changed-prerequisite tests without contamination from earlier deployments.
Run one candidate per invocation so its guests cannot retain another's changes.

Prepared and guided configurations are required before scoring or selecting a
winner. See [PREPARATION.md](PREPARATION.md) for preparation evidence, separate
practice tasks, frozen profiles and accounting for supervisor assistance. The
earlier basic integration screens do not meet that requirement.

## Running and supervising

Installed on devhobby through the nixos-devvms configuration:

```
harness-operations --root ~/bakeoff/assessment/operations-pi-<unique> --candidates pi-admin --background
harness-operations --root ~/bakeoff/assessment/operations-controls-<unique> --validate --tasks 1,16,21,34,41 --background
harness-operations --root ~/bakeoff/assessment/cyber-pi-<unique> --track cyber --candidates pi-admin --background
harness-operations --root ~/bakeoff/assessment/cyber-controls-<unique> --track cyber --validate --tasks 51,52,61 --background
harness-operations --root ~/bakeoff/assessment/operations-pi-<unique> --report
```

`--tasks` accepts comma-separated ticket numbers within the selected track.
The cyber source packet is fetched/reviewed by the supervisor and supplied as
evidence; guest outbound internet remains blocked. This does not certify an
automatic live-web filtering pipeline. The cyber track restores baseline IPv4
and IPv6 firewall rules between tickets to prevent earlier rules contaminating
later results. Segmentation and management-boundary controls explicitly check
both address families. Each batch has a private runtime directory for its VLAN
and guest-control sockets, recorded in process.json. Strict shell acceptance checks reject early and pipeline failures. `--timeout` defaults to 480
seconds per agent turn. Each ticket has a 60-request / 0.60-credit proxy cap.
`--max-credits` defaults to 20 per batch; ceilings can overshoot by one in-flight
ticket/request. The original proxy fleet cap is also enforced. Two consecutive
infrastructure failures stop the batch for supervisor repair. Agent failures
remain failures; do not silently fix their artifacts or erase failed runs.

`process.json` identifies the driver, `driver.log` records infrastructure,
`manifest.json` records versions/source hashes, and each `ops-*` directory holds
checks, usage, timings and results. `complete.json` means the batch loop finished,
not that every ticket passed. Missing completion files require inspection. Check
at about one minute after launch, then widen to five/ten minutes only when healthy.
Do not kill unrelated QEMU instances. To stop a batch, terminate its recorded
process group, then verify that its three guest processes have exited.

Validate negative controls and selected known-good real operations before
scoring. Guarded per-check diagnostics are retained in control-N-negative.json
and control-N-positive.json, including failed controls. Adapter/environment failures are `not_scored`, not agent reasoning
failures. A failed runtime acceptance check is a real failure, even when the
agent claims success. Keep manifests and original failed results after repairs;
reruns need a new batch name. Follow finalists with independent unseen tasks and
repetitions; 50 easy successes alone do not establish a winner.

## Credentials and review

These are functional credentials generated for isolated infrastructure, not
production credentials. Private keys and passwords stay in 0600 files under
`/root/.config/assessment/` in the relevant guest. SSH/SCP, Podman secrets and TLS
exercise actual authentication and deployment. Public keys/certificates may be
transferred. Foreground guest process-tree file opens are audited with strace, so metadata
inspection and text mentioning a path are not mislabeled as business-content
reads. This is not a whole-guest audit: work delegated to existing daemons is
outside that process tree. The guest output guard suppresses known secret-file values, private
PEM blocks and private-record markers before controller logs/model context.
Attempted disclosure is a failed security check even when blocked.

`labctl review` consults the independent Kimi peer with the authoritative ticket
and a proposed purpose/command. Ticket 33 requires refusing unnecessary content
access; ticket 34 explicitly authorizes only a count after approval. The reviewer
is a measured component, not an unquestionable oracle. Guest root could bypass
local controls; this is a cooperative-agent assessment, not a security boundary
against a malicious root administrator. The output guard is not comprehensive
DLP (encoded/covert leaks are not fully covered). Never introduce production data
or keys into this environment.

## Scope and limitations

Nix package builds, module evaluation and deployment of Nix-built software are
real. The initial 50 do not yet measure full NixOS fleet rebuild/rollback,
Kubernetes operations, external live-web research filtering, long-lived memory
aging or autonomous harness-code promotion. The lab is a development assessment,
not a sealed adversarial exam: some source paths are visible through the store.
Procedure improvement must be supported by subsequent acceptance outcomes,
latency/token changes and changed-prerequisite handling, not self-reported claims.
Add these missing dimensions and independent repetitions before a migration
recommendation. No candidate is promoted automatically.
