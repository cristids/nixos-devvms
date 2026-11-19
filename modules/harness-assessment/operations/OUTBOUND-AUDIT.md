# Outbound traffic audit — all contenders

User requirement, 2026-11-19: examine every external destination, not just
Parallel.ai. This is required before a deployment recommendation. No contender
currently has a completed destination audit. A brokered model response or a
blocked network namespace does not establish absence of unwanted attempts.

## Permitted assessment routes

Candidate inference goes to the isolated local broker, which forwards bounded
Chat Completions requests to the authorized Melious route. Independent Claude
purpose review is a separate controller-only bridge; the candidate never receives
its credential or socket. Synthetic guest operations go through labctl. Public
research performed by the supervising evaluator is distinct from candidate egress.
Any further service requires an explicit recorded purpose and authorization.

## Current static configuration evidence

These observations come from assess.py/native_profiles.py at e595ff8. They are
configuration evidence, not verified upstream defaults or runtime destination
captures. Common environment flags are DO_NOT_TRACK and OTEL_SDK_DISABLED;
individual implementations may ignore them.

| Contender | Configured restrictions | Audit still required |
|---|---|---|
| Pi / improved Pi | Assessment provider, telemetry/install analytics off, version check skipped | Extension/gate/memory auxiliary calls, fallback behavior, default upstream behavior |
| OMP | Explicit broker model roles, local memory, offline, restricted tools without web search | Upstream default web chain starts with Parallel; anonymous searches enabled by default. Other background endpoints still unaudited |
| Hermes | Custom broker model, shared metrics off, selected local tools | Memory/compression/title/reviewer provider inheritance, updates, hidden auxiliary requests |
| OpenCode | Broker models, sharing/update/model-fetch disabled | Plugins, package resolution, external tool and runtime fallback destinations |
| Goose | OpenAI broker, telemetry off, keyring off | Extension startup, memory services, provider fallback and update traffic |
| Vibe | Generic broker, telemetry off, title generation off | Auxiliary inference, tools, updates and provider fallbacks |
| Letta Code | Local backend, broker connection, updater and Scarf disabled | Local memory hooks, backend fallbacks, other analytics and auxiliary inference |
| OpenClaw | Explicit openclaw runtime/provider, memory search provider none | Tools/skills, model catalog and update lookups, analytics and other memory routes |
| Qwen Code | Custom broker providers, telemetry off | Auto-memory/auto-dream/auto-skill provider inheritance, search/fetch, updates and fallback |
| Prime Agent | Explicit broker provider, offline, Nix kernel | Bundled skills/refine, subprocesses, memory/subagent routes, default cloud integrations |
| Deep Agents Code | Explicit broker Chat Completions, LangSmith tracing off, MCP disabled | LangGraph child environment, tool services, telemetry and fallback routes |
| Kimi Code | Package only; no assessment adapter yet | Full provider/telemetry/search/memory/subagent audit before qualification |
| Other landscape candidates | Not yet prepared | Same audit required if selected; absence of adapter is not a pass or failure |

OMP upstream evidence, pinned v18.22.2:
https://github.com/can1357/oh-my-pi/blob/v18.22.2/docs/tools/web_search.md
The default web role starts with web/parallel; providers.webKeyless defaults true.
The public MCP search sends objective/search_queries, which themselves can contain
sensitive content. This does not mean all LLM traffic uses Parallel. Our OMP
assessment disables web_search and uses an isolated network namespace.

## Runtime evidence required for each version/profile

Exercise startup, ordinary inference, delegated inference, web search/fetch,
memory creation/retrieval/compaction, skills/MCP/plugin activation, error handling,
provider fallback, session end and idle/background work. Include both upstream
fresh defaults and the prepared profile. Never put production credentials or
business data in these probes; use only synthetic fixtures and dummy keys.

Collect destination and outcome metadata (DNS names, IP/port, endpoint category,
calling process/component, trigger, allowed/blocked/successful). Avoid request
bodies, authentication headers, raw environment dumps and actual secret values.
No transparent forwarding to arbitrary services merely to observe behavior.
Use an isolated deny-by-default network and approved broker routes. A failed DNS
lookup or connection must remain evidence of an attempted external dependency.
Test the observation mechanism with known positive and blocked controls, and
check it does not change daemon lifetimes (the strace foreground bug is a warning).

Report separately:

- Default external destinations and the source/version proving them.
- Prepared configuration and supported controls that disable or redirect them.
- Runtime attempts, successful traffic and remaining unexercised paths.
- Model identity/fallback observed at the authorized broker.
- Deployment conditions or unresolved risks; never infer safe production defaults
  from isolation supplied by the evaluation harness.

## Model comparison sequence

User confirmed on 2026-11-19: finish the GLM-5.3 worker round, then repeat with
DSF. Keep reviewers and task contracts fixed and start from copies of the same
prepared profiles; do not import scored GLM solutions into the DSF round.
Confirm the exact DSF model identifier against the available catalog before
launch. Report outcomes, spend and latency separately. No comparative winner is
established by the current preparation coverage.
