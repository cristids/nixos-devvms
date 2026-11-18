# Harness landscape review — 2026-11-18

The prior 13-product list was incomplete. This is a source-based screening inventory,
not a performance ranking or a claim that every possible agent has been found.
Research covered terminal coding agents, persistent assistants, execution workspaces,
vendor agents, and orchestration/research layers. Primary repositories and official
docs were checked after discovery searches. API rate limits affected some metadata
requests; missing metadata is not evidence of abandonment. Raw source snapshots are
retained in the private assessment handoff directory under landscape-20261118.

## Practical selection priorities

Retain installed Pi-admin, Hermes, OpenCode, Goose, Vibe and DSH. Preserve old Pi
as a separate baseline. Letta Code and OpenClaw remain important outstanding trials.
The following additions deserve setup/fit checks before any overall recommendation.
Not all need the full task battery: record a specific reason for scoped testing or
deferral. No new candidate has yet failed a benchmark on devhobby.

| Addition and primary source | Reason to evaluate / concrete preparation requirement |
|---|---|
| [Oh My Pi](https://github.com/can1357/oh-my-pi) | Pi-derived runtime with native memory and managed skills. Memory and autolearn are off by default; enable a local backend deliberately. Audit web-search egress before use. |
| [Prime Agent](https://github.com/PrimeIntellect-ai/prime-agent) | RLM execution and a continual harness that refines memory, prompts and skills. Its worker processes are explicitly not a security sandbox; use the external lab boundary. |
| [Deep Agents Code](https://github.com/langchain-ai/deepagents/blob/main/libs/README.md) | Concrete terminal agent (`dcode`), not only the LangChain framework; memory, skills and headless execution. Verify storage backend and tracing settings. |
| [Qwen Code](https://github.com/QwenLM/qwen-code) | Multi-protocol agent with Auto-Memory, skills and subagents. Distinguish stable version from the Qoder-derived 1.0 preview. |
| [Kimi Code CLI](https://github.com/MoonshotAI/kimi-code) | Current terminal agent supports compatible providers, plugins and lifecycle hooks. Avoid superseded Python kimi-cli. |
| [Cline](https://github.com/cline/cline) | Now has a CLI and SDK with the same engine; excluding it as IDE-only would be incorrect. |
| [Kilo Code](https://github.com/Kilo-Org/kilocode) | CLI with model choice and agent workflows; pin the actual CLI implementation rather than assuming extension behavior. |
| [Crush](https://github.com/charmbracelet/crush) | Multi-provider terminal alternative. License is FSL-1.1-MIT, not an ordinary current MIT grant; document that distinction. |
| [VT Code](https://github.com/vinhnx/VTCode) | Rust runtime with persistent memory, skills and provider controls; verify the configured boundary in practice. |
| [QwenPaw, formerly CoPaw](https://github.com/agentscope-ai/QwenPaw) | Persistent assistant with layered memory and local/cloud routes; compare learning and administration behavior. |
| [IronClaw](https://github.com/nearai/ironclaw) | Persistent assistant with capability-based WASM tools and Docker jobs; determine which administration tasks require broader execution. |
| [ZeroClaw](https://github.com/zeroclaw-labs/zeroclaw) | Self-hosted assistant infrastructure; inspect memory and permissions before a focused trial. |
| [nanobot](https://github.com/HKUDS/nanobot) | Lightweight persistent assistant; useful contrast to larger OpenClaw-style stacks. |
| [PicoClaw](https://github.com/sipeed/picoclaw) | Resource-focused assistant; lower priority unless its simpler deployment preserves needed capabilities. |

Prioritize OMP, Prime Agent, Deep Agents, Qwen Code and QwenPaw alongside Letta,
Hermes and OpenClaw for the learning question. This priority reflects documented
fit, not validated quality. Cline/Kilo/Kimi/Crush/VT Code remain real alternatives.

## Vendor and model-route candidates

Do not silently discard these as IDE-only or assume they require a particular
subscription. Check Linux headless operation, approved model route, storage,
telemetry and accounting before deciding on a practical trial.

| Candidate | Official evidence / limitation to resolve |
|---|---|
| [Factory Droid](https://docs.factory.com/model-independence/byok) | CLI supports custom-model BYOK; promising approved-route candidate. |
| [Junie CLI](https://github.com/JetBrains/junie) | Terminal agent with BYOK for several providers; check custom endpoint and Nix packaging. |
| [Grok Build](https://github.com/xai-org/grok-build) | Public terminal harness; validate custom endpoint and protocol support. |
| [Antigravity CLI](https://github.com/google-antigravity/antigravity-cli) | Current Google terminal agent; provider-route constraints need checking. |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Current README describes enterprise/legacy use and warns it is not tuned for Gemini 4+; do not use stale free-tier assumptions. |
| [Amp](https://ampcode.com/security) | Assess server-side thread storage and model routing before private-memory comparison. |
| [Auggie](https://www.augmentcode.com/changelog/auggie-cli-1-2-0-release-notes) | Current 1.2 release is Pi-based; record lineage and service differences. |
| [Cursor CLI](https://cursor.com/docs/cli/overview) | A real terminal agent, but privacy mode is not proof of local-only storage. |
| [Kiro CLI](https://kiro.dev/docs/cli/) | Custom agents, tools and steering; check provider/account route. |
| [GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/overview) | Instructions, skills and MCP; verify data flow and available inference route. |
| [Qoder CLI](https://docs.qoder.com/cli/overview) | Headless agent with tools and memory; distinguish from Qwen Code preview lineage. |
| [Warp Agent CLI](https://docs.warp.dev/agents/cli/) | Standalone terminal runner exists; distinguish its own agent from third-party agents it hosts. |
| [TraeCode](https://www.trae.ai/cli) | CLI product exists; detailed route/privacy fit not yet established. |

## Retained specialist candidates and non-equivalent layers

- [OpenHands](https://github.com/OpenHands/software-agent-sdk): runtime/workspace
  candidate; SDK and application are one family, not two independent harnesses.
- [Odysseus](https://github.com/odysseus-dev/odysseus): workspace candidate;
  earlier architectural reservations are not runtime failures.
- [Aider](https://github.com/Aider-AI/aider): focused coding baseline; reduced
  recent repository activity alone does not prove unusability.
- [fx](https://github.com/vercel-labs/fx) and [Jcode](https://github.com/1jehuang/jcode):
  retain focused execution/permission and multi-agent investigations.
- [SWE-agent](https://github.com/SWE-agent/SWE-agent) and
  [mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent): scoped coding or
  security-task baselines, not automatically persistent administration systems.
- [NanoClaw](https://github.com/nanocoai/nanoclaw): isolation/orchestration around
  another harness. Default Claude-based path is outside the user-selected scope;
  an OpenCode option is a deployment variant, not a new independent core.
- [Ruflo](https://github.com/ruvnet/ruflo): orchestration/integration layer; compare
  a specified underlying runner configuration rather than count it twice.
- [Fabric](https://github.com/danielmiessler/Fabric): prompt-pattern framework,
  not a direct autonomous administration replacement.
- [HarnessEvolve](https://arxiv.org/abs/2609.00829): research methodology to verify,
  introduced by the user's uploaded report; not a ready runner established by this
  review. Use independent gates, held-out tasks and regression checks as design
  requirements without treating author-reported gains as our measured results.

## Explicit exclusions and lifecycle corrections

Claude Code and Codex are excluded by user instruction. Claude API is separately
authorized for other harnesses. [Open Interpreter](https://github.com/openinterpreter/openinterpreter)
now identifies itself as a Codex fork; defer it from the immediate practical roster
and disclose that lineage rather than silently reintroducing Codex.
[Roo Code](https://github.com/RooCodeInc/Roo-Code) is archived.
[Continue](https://github.com/continuedev/continue) explicitly says it is no longer
actively maintained/read-only despite GitHub metadata not marking it archived.
These are maintenance/selection reasons, not failed assessment scores.

## Security and model configuration are part of preparation

Evaluate both harness security and its ability to perform security work. Cover
prompt injection, unauthorized business reads, secret exposure, tool/network
boundaries, memory/skill poisoning, stale procedure invalidation, privilege
changes, provenance and rollback. Local memory does not imply local inference.
A successful ordinary task does not establish resistance to malicious content.

A concrete OMP finding requires verification at the selected version: current
[Parallel search source](https://github.com/can1357/oh-my-pi/blob/main/packages/coding-agent/src/web/search/providers/parallel.ts)
contains a keyless public MCP fallback and reports the provider available without
a credential. [PR 13693](https://github.com/can1357/oh-my-pi/pull/13693) proposes
removing that fallback. This is evidence of a query egress path, not proof of
arbitrary workspace exfiltration. Pin/restrict search routes and observe traffic.

Melious's live catalog was queried through the existing broker on 2026-11-18;
it includes GLM 5.3/Flash, Kimi K3/K2.7 Code, DeepSeek V4.1 Flash, Qwen3.8 Max,
Qwen3 Coder Next and Mistral Small 4, among others. Start with supported stronger
models for reasoning/review and cheaper models for routine work when the harness
supports role assignment. Compare supported configurations, retain a common-model
diagnostic, and record model identity, cost, assistance and native-feature settings.
Claude API authorization does not yet mean its isolated broker route is implemented.
Do not expose real provider credentials to candidate processes to bypass that work.

The user's uploaded self-learning report is supplementary evidence, explicitly a
reconstruction. Its architectural recommendations are not installed features or
verified devhobby results. Preserve separate evidence for capture, cross-session
retrieval, successful reuse, changed prerequisites, and regression-free promotion.

Paseo is the integration target. Herdr in the supplied report is historical/source context only.

## Stable CLI release check, 2026-11-18 continuation

GitHub release lists identify Qwen Code **v0.28.0**, Prime Agent **v0.10.5**,
and Deep Agents Code **deepagents-code==0.2.11** as their current stable CLI
releases. Qwen SDK/desktop tags and the Deep Agents library tag are different
components and must not substitute for the terminal harness version.
OpenClaw2026.11.5 and Letta Code0.34.65 are now packaged and in native practice;
this is preparation progress, not evidence that the remaining candidates failed.
Qwen Code0.28.0's published npm entry is `cli-entry.js` (different from the
source package manifest). Use Node26.13.1: its CLI warns of excessive transient
shell-safety memory usage on Node24 and recommends at least26.12.1.
