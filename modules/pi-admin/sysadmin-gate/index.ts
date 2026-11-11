// sysadmin-gate: pi extension for pi-admin on devpro (copied from nixos_llm_srv
// modules/ai-services/pi-admin, 2026-11-11; keep the two copies in step).
//
//  bash, in this order:
//    destructive         → the user, red DESTRUCTIVE warning, never auto, never remembered
//    read-only allowlist → bwrap with a read-only filesystem, no prompt
//                          (zpool/zfs/podman read verbs: unsandboxed — see classifier.ts)
//    saved rule          → unsandboxed, no prompt (rules: ${STATE}/allow-rules.json)
//    anything else, not needing the host (builds, tests, git commit, …)
//                        → project sandbox (start project writable, rest read-only), no prompt
//    the rest (ssh, remote git, podman, systemctl, "# pi-admin: unsandboxed" first line)
//                        → reviewer model (a different model than the session's);
//                          allow → unsandboxed; otherwise the user: once / always / no
//  edit/write  inside the start project (git top level of the start folder, never
//              $HOME or above) or the config repo (symlinks resolved, .git/
//              excluded) → allowed; elsewhere → confirm
//  read/grep/find/ls + the read-only MCP tools (searxng search/url read, context7,
//  mcp-nixos, ERPNext reads; exact names in classifier.ts toolNeedsConfirm) → allowed;
//  any other tool (other MCP tools, unknown tools) → confirm
//  no interactive TUI (print/json/rpc) → block everything not auto-allowed
//
// Classifier lives in classifier.ts (unit tests: classifier.test.ts).

import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, realpathSync, renameSync, statSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { homedir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import {
	CACHE_SUBDIRS, REVIEW_SYSTEM, bwrapArgv, bwrapProjectArgv, classifyBash, isInsideRoot, matchesRules,
	needsHost, parseVerdict, pickReviewer, projectRoot, ruleKeysFor, toolNeedsConfirm,
} from "./classifier.ts";

const REPO = process.env.PI_ADMIN_REPO ?? "/home/cristian/development/nixos-devvms";
const BWRAP = process.env.PI_ADMIN_BWRAP;
const BASH = process.env.PI_ADMIN_BASH;
const STATE = process.env.PI_ADMIN_STATE ?? "/var/lib/pi-admin";
const RULES_FILE = `${STATE}/allow-rules.json`;
const REVIEW_LOG = `${STATE}/review.log`;
const REVIEW_URL = process.env.PI_ADMIN_REVIEW_URL ?? "https://api.melious.ai/v1/chat/completions";
const REVIEW_MODELS = (process.env.PI_ADMIN_REVIEW_MODELS ?? "glm-5.3,kimi-k3").split(",").map((m) => m.trim()).filter(Boolean);
const GLOBAL_RULES = (() => {
	try {
		return readFileSync(join(process.env.PI_CODING_AGENT_DIR ?? "", "AGENTS.md"), "utf8").slice(0, 8000);
	} catch {
		return "";
	}
})();
// The agent asks for an unsandboxed run (still reviewed / asked) with this first line.
const UNSANDBOXED_MARKER = /^#\s*pi-admin:\s*unsandboxed[^\n]*\n/;

function real(p: string): string {
	try {
		return realpathSync(p);
	} catch {
		return p;
	}
}

// Auto-edit roots: the project pi-admin was started in (its git top level, or
// the folder itself; never $HOME or above) plus the devpro config repo.
// Fixed at startup, so a later `cd` in bash cannot widen them.
const START = real(process.cwd());
const PROJECT = projectRoot(START, real(homedir()), (d) => existsSync(join(d, ".git")));
const ROOTS = [...new Set([PROJECT, real(REPO)].filter((r): r is string => r !== null))];

// Absolute path with every existing component's symlinks resolved.
function resolveTarget(p: string, cwd: string): string {
	let s = p.startsWith("@") ? p.slice(1) : p;
	if (s === "~" || s.startsWith("~/")) s = join(homedir(), s.slice(1));
	const abs = resolve(cwd, s);
	let head = abs;
	const tail: string[] = [];
	while (!existsSync(head)) {
		const parent = dirname(head);
		if (parent === head) break;
		tail.unshift(basename(head));
		head = parent;
	}
	try {
		head = realpathSync(head);
	} catch {
		/* keep */
	}
	return tail.length ? join(head, ...tail) : head;
}

function sandboxDevices(): string[] {
	const out: string[] = [];
	if (existsSync("/dev/zfs")) out.push("/dev/zfs");
	try {
		for (const n of readdirSync("/dev")) if (n.startsWith("nvidia")) out.push(`/dev/${n}`);
	} catch {
		/* ignore */
	}
	return out;
}

// ---- saved "don't ask again" rules: {"rules": ["ssh root@host", …]}
function loadRules(): string[] {
	try {
		const j = JSON.parse(readFileSync(RULES_FILE, "utf8"));
		return Array.isArray(j.rules) ? j.rules.filter((r: unknown) => typeof r === "string") : [];
	} catch {
		return [];
	}
}
function saveRules(add: string[]) {
	const rules = [...new Set([...loadRules(), ...add])].sort();
	const tmp = `${RULES_FILE}.tmp`;
	writeFileSync(tmp, JSON.stringify({ rules }, null, 2) + "\n", { mode: 0o600 });
	renameSync(tmp, RULES_FILE);
}

// ---- project sandbox: per-project cache dir and read-only .git pieces
function projectCache(root: string): string {
	const dir = `${STATE}/cache/${createHash("sha256").update(root).digest("hex").slice(0, 16)}`;
	for (const d of CACHE_SUBDIRS) mkdirSync(join(dir, d), { recursive: true, mode: 0o700 });
	return dir;
}
// .git/hooks and .git/config of the project and its submodules. A missing hooks
// dir is created first, otherwise the sandbox could create it with a hook inside.
function gitProtected(root: string): string[] {
	const out: string[] = [];
	const visit = (gitDir: string, depth: number) => {
		try {
			if (!statSync(gitDir).isDirectory()) return;
		} catch {
			return;
		}
		mkdirSync(join(gitDir, "hooks"), { recursive: true });
		out.push(join(gitDir, "hooks"));
		if (existsSync(join(gitDir, "config"))) out.push(join(gitDir, "config"));
		if (depth > 3) return;
		const mods = join(gitDir, "modules");
		try {
			for (const n of readdirSync(mods)) visit(join(mods, n), depth + 1);
		} catch {
			/* no submodules */
		}
	};
	visit(join(root, ".git"), 0);
	return out;
}

// ---- reviewer
function text(content: any): string {
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return "";
	return content.filter((c) => c?.type === "text").map((c) => c.text).join("\n");
}
// User messages and the agent's tool calls, never tool results or model text.
function transcript(ctx: any): string {
	let entries: any[] = [];
	try {
		entries = ctx.sessionManager.getBranch();
	} catch {
		/* none */
	}
	const lines: string[] = [];
	for (const e of entries) {
		if (e?.type !== "message") continue;
		const m = e.message;
		if (m?.role === "user") lines.push(`USER: ${text(m.content).slice(0, 2000)}`);
		else if (m?.role === "assistant" && Array.isArray(m.content))
			for (const c of m.content)
				if (c?.type === "toolCall") lines.push(`AGENT TOOL CALL ${c.name}: ${JSON.stringify(c.arguments).slice(0, 600)}`);
	}
	let out = lines.slice(-60).join("\n");
	if (out.length > 24000) out = "…" + out.slice(-24000);
	return out;
}

async function review(ctx: any, cmd: string, why: string): Promise<{ decision: "allow" | "ask"; reason: string; model: string | null }> {
	const model = pickReviewer(REVIEW_MODELS, ctx.model?.id);
	const key = process.env.MELIOUS_API_KEY;
	if (!model || !key) return { decision: "ask", reason: "no reviewer model available", model };
	const user = [
		"# Rules the agent works under",
		GLOBAL_RULES || "(none)",
		"# Transcript (oldest first)",
		transcript(ctx) || "(empty)",
		"# Command to judge",
		`cwd: ${ctx.cwd ?? process.cwd()}`,
		`why it is not auto-allowed: ${why}`,
		"<command>",
		cmd,
		"</command>",
	].join("\n\n");
	const abort = AbortSignal.timeout(60000);
	const signal = ctx.signal ? AbortSignal.any([ctx.signal, abort]) : abort;
	try {
		const r = await fetch(REVIEW_URL, {
			method: "POST",
			signal,
			headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
			body: JSON.stringify({
				model,
				temperature: 0,
				max_tokens: 4096,
				messages: [
					{ role: "system", content: REVIEW_SYSTEM },
					{ role: "user", content: user },
				],
			}),
		});
		if (!r.ok) return { decision: "ask", reason: `reviewer HTTP ${r.status}`, model };
		const j: any = await r.json();
		return { ...parseVerdict(String(j?.choices?.[0]?.message?.content ?? "")), model };
	} catch (e: any) {
		return { decision: "ask", reason: `reviewer unavailable (${e?.name ?? "error"})`, model };
	}
}

function logReview(entry: object) {
	try {
		appendFileSync(REVIEW_LOG, JSON.stringify({ t: new Date().toISOString(), ...entry }) + "\n", { mode: 0o600 });
	} catch {
		/* best effort */
	}
}

export default function (pi: any) {
	const stats = { autoTools: 0, sandboxed: 0, hostReadOnly: 0, project: 0, rules: 0, reviewAllowed: 0, reviewAsked: 0, approved: 0, denied: 0, blocked: 0, autoEdits: 0 };
	const interactive = (ctx: any) => ctx.hasUI && (ctx.mode === undefined || ctx.mode === "tui");
	const red = (ctx: any, s: string) => {
		try {
			return ctx.ui.theme.bold(ctx.ui.theme.fg("error", s));
		} catch {
			return `\x1b[1;31m${s}\x1b[0m`;
		}
	};

	async function ask(ctx: any, title: string, body: string, why: string) {
		if (!interactive(ctx)) {
			stats.blocked++;
			return { block: true, reason: `sysadmin-gate: ${why}; blocked (no interactive UI to confirm)` };
		}
		const ok = await ctx.ui.confirm(title, body);
		if (!ok) {
			stats.denied++;
			return { block: true, reason: "sysadmin-gate: denied by the user" };
		}
		stats.approved++;
		return null;
	}

	pi.on("session_start", async (_e: any, ctx: any) => {
		if (ctx.hasUI) ctx.ui.setStatus("sysadmin-gate", "🛡 gate on");
	});

	pi.registerCommand("gate", {
		description: "Show the sysadmin-gate status",
		handler: async (_args: string, ctx: any) => {
			const rules = loadRules();
			const msg = [
				"sysadmin-gate ACTIVE",
				`sandbox: ${BWRAP ? "bwrap" : "UNAVAILABLE (read-only commands will prompt)"}`,
				`auto-edit roots: ${ROOTS.join(", ")} (.git/ excluded)` +
					(PROJECT ? "" : `\nstart folder ${START} is $HOME or above: edits there ask`),
				`project sandbox: ${PROJECT && BWRAP ? `${PROJECT} writable` : "off"}`,
				`reviewer: ${pickReviewer(REVIEW_MODELS, ctx.model?.id) ?? "none (everything asks)"}; log ${REVIEW_LOG}`,
				`saved rules (${RULES_FILE}): ${rules.length ? rules.join(" | ") : "none"}`,
				`this session: ${stats.sandboxed} read-only sandbox, ${stats.hostReadOnly} host read-only, ${stats.project} project sandbox, ` +
					`${stats.rules} by rule, ${stats.reviewAllowed} reviewer-allowed, ${stats.reviewAsked} reviewer-asked, ` +
					`${stats.approved} approved, ${stats.denied} denied, ${stats.blocked} blocked, ${stats.autoEdits} auto edits, ` +
					`${stats.autoTools} auto read-only tool calls`,
			].join("\n");
			if (ctx.hasUI) ctx.ui.notify(msg, "info");
			else console.log(msg);
		},
	});

	pi.on("tool_call", async (event: any, ctx: any) => {
		const tool: string = event.toolName;
		if (tool === "bash") {
			let cmd: string = String(event.input.command ?? "");
			const wantsHost = UNSANDBOXED_MARKER.test(cmd);
			if (wantsHost) cmd = cmd.replace(UNSANDBOXED_MARKER, "");
			const unsandboxed = () => {
				// never hand the provider key to the command
				event.input.command = `unset MELIOUS_API_KEY\n${cmd}`;
				return undefined;
			};
			const v = classifyBash(cmd);

			// 1. destructive: always the user, red warning, never remembered
			if (v.kind === "destructive") {
				const res = await ask(
					ctx,
					red(ctx, "⚠ DESTRUCTIVE COMMAND"),
					`${red(ctx, "DESTRUCTIVE: " + v.reasons.join(", "))}\n\n  ${cmd}\n\nRun it UNSANDBOXED?`,
					`destructive command (${v.reasons.join(", ")})`,
				);
				return res ?? unsandboxed();
			}
			// 2. read-only allowlist
			if (v.kind === "sandbox" && BWRAP && BASH && !wantsHost) {
				stats.sandboxed++;
				event.input.command = bwrapArgv(cmd, { bwrap: BWRAP, bash: BASH, devices: sandboxDevices() });
				return undefined;
			}
			if (v.kind === "host") {
				stats.hostReadOnly++;
				return unsandboxed();
			}
			// 3. saved "don't ask again" rule
			if (matchesRules(cmd, loadRules())) {
				stats.rules++;
				return unsandboxed();
			}
			const why = v.kind === "confirm" ? v.reason : "sandbox unavailable";
			// 4. dev work in the project sandbox
			const host = needsHost(cmd);
			if (PROJECT && BWRAP && BASH && !host && !wantsHost) {
				try {
					event.input.command = bwrapProjectArgv(cmd, {
						bwrap: BWRAP, bash: BASH, devices: sandboxDevices(), root: PROJECT,
						cache: projectCache(PROJECT), roBinds: gitProtected(PROJECT), uid: process.getuid?.() ?? 1000,
					});
					stats.project++;
					return undefined;
				} catch {
					/* could not prepare the sandbox: review instead */
				}
			}
			// 5. reviewer model, then the user
			const reason = wantsHost ? "agent asked to run it unsandboxed" : host ? `needs the host (${host})` : why;
			const r = await review(ctx, cmd, reason);
			logReview({ cwd: ctx.cwd, model: r.model, decision: r.decision, reason: r.reason, cmd: cmd.slice(0, 2000) });
			if (r.decision === "allow") {
				stats.reviewAllowed++;
				if (ctx.hasUI) ctx.ui.notify(`gate: ${r.model} allowed: ${r.reason}`, "info");
				return unsandboxed();
			}
			stats.reviewAsked++;
			if (!interactive(ctx)) {
				stats.blocked++;
				return { block: true, reason: `sysadmin-gate: reviewer did not allow it (${r.reason}); blocked (no interactive UI to confirm)` };
			}
			const keys = ruleKeysFor(cmd);
			const ONCE = "Yes, run it once";
			const ALWAYS = keys ? `Yes, and don't ask again for: ${keys.join(", ")}` : null;
			const NO = "No";
			const choice = await ctx.ui.select(
				`Run bash command? (unsandboxed)\n\n  ${cmd}\n\n${reason}\nreviewer ${r.model ?? "-"}: ${r.reason}`,
				ALWAYS ? [ONCE, ALWAYS, NO] : [ONCE, NO],
			);
			if (choice === ONCE || (ALWAYS && choice === ALWAYS)) {
				stats.approved++;
				if (choice === ALWAYS && keys) {
					try {
						saveRules(keys);
					} catch (e: any) {
						ctx.ui.notify(`gate: could not save rule: ${e?.message ?? e}`, "error");
					}
				}
				return unsandboxed();
			}
			stats.denied++;
			return { block: true, reason: "sysadmin-gate: denied by the user" };
		}

		if (tool === "edit" || tool === "write") {
			const p = String(event.input.path ?? "");
			const target = resolveTarget(p, ctx.cwd ?? process.cwd());
			event.input.path = target; // pi executes exactly the path we checked
			if (ROOTS.some((r) => isInsideRoot(target, r))) {
				stats.autoEdits++;
				return undefined;
			}
			return (
				(await ask(ctx, `Allow ${tool} outside the project?`, target, `${tool} outside ${ROOTS.join(", ")} (or inside a .git/)`)) ??
				undefined
			);
		}

		const why = toolNeedsConfirm(tool, event.input);
		if (why === null) {
			stats.autoTools++;
			return undefined;
		}
		return (
			(await ask(ctx, `Allow tool "${tool}"?`, JSON.stringify(event.input ?? {}, null, 2).slice(0, 2000), why)) ??
			undefined
		);
	});
}

