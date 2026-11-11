// node --test classifier.test.ts   (Node ≥ 23 strips the types natively)
import { test } from "node:test";
import assert from "node:assert/strict";
import { bwrapArgv, classifyBash, isInsideRoot } from "./classifier.ts";

const kind = (c: string) => classifyBash(c).kind;

test("allowlisted read-only commands are sandboxed", () => {
	for (const c of [
		"ls -la /etc",
		"cat /etc/os-release",
		"head -n 20 configuration.nix",
		"grep -rn llama-swap modules/",
		"rg 'pi-admin' -n",
		"find . -name '*.nix' -type f",
		"stat /tank",
		"df -h",
		"du -sh /var/lib/*",
		"free -g",
		"ps aux",
		"ss -tlnp",
		"ip addr show",
		"ip -br link",
		"ip route",
		"systemctl status llama-swap",
		"systemctl --no-pager list-units --failed",
		"systemctl cat strata.service",
		"systemctl is-active sshd",
		"journalctl -u llama-swap -n 50 --no-pager",
		"git status",
		"git -C /etc/nixos log --oneline -5",
		"git diff HEAD~1",
		"git branch -a",
		"nix eval .#nixosConfigurations.cdssrv02.config.networking.hostName",
		"nix flake show",
		"nix path-info -rS /run/current-system",
		"nix why-depends /run/current-system nixpkgs#hello",
		"nvidia-smi",
		"nvidia-smi --query-gpu=name,memory.used --format=csv",
		"readlink -f /etc/nixos",
		"which pi",
		"wc -l flake.lock",
		"jq .version package.json",
		"diff a b",
	]) assert.equal(kind(c), "sandbox", c);
});

test("zfs/zpool/podman read verbs run on the host namespaces (no prompt)", () => {
	for (const c of ["zpool status -x", "zpool list -H -o name", "zfs list -o name,used", "zfs get compression tank",
		"podman ps -a", "podman inspect searxng | jq '.[0].State'", "podman logs --tail 20 searxng"])
		assert.equal(kind(c), "host", c);
	assert.equal(kind("zpool list > /tmp/x"), "confirm");
	assert.equal(kind("zfs list; touch x"), "confirm");
});

test("compound commands: every segment must be allowlisted", () => {
	assert.equal(kind("ps aux | grep llama | sort | uniq -c"), "sandbox");
	assert.equal(kind("df -h && free -g; zpool list || true"), "confirm"); // true not allowlisted
	assert.equal(kind("df -h && free -g; zpool list"), "host");
	assert.equal(kind("journalctl -u x 2>&1 | tail -5"), "sandbox");
	assert.equal(kind("ls /nope 2>/dev/null"), "sandbox");
	assert.equal(kind("ls | xargs cat"), "confirm");
	assert.equal(kind("cat x | sh"), "confirm");
	assert.equal(kind("ls\ncurl http://x"), "confirm");
});

test("redirection is never auto-allowed", () => {
	for (const c of ["ls > out.txt", "cat a >> b", "ls | tee out", "echo x >/etc/foo", "cat < /etc/shadow",
		"ls 2>err.log", "ls &> out", "ls >| f"])
		assert.notEqual(kind(c), "sandbox", c);
});

test("substitution, heredocs, subshells, background are never auto-allowed", () => {
	for (const c of [
		"echo $(id)", "echo `id`", "cat <(ls)", "diff <(ls a) <(ls b)", "cat <<EOF\nx\nEOF", "cat <<< x",
		"echo $HOME", 'echo "$(whoami)"', "(ls)", "ls &", "ls & rm x", "! ls", "ls # comment",
		"FOO=1 ls", "/bin/ls",
	]) assert.notEqual(kind(c), "sandbox", c);
	assert.equal(kind("echo $(rm -rf /)"), "destructive");
	assert.equal(kind("echo `reboot`"), "destructive");
});

test("allowlisted commands with exec/write options are not auto-allowed", () => {
	for (const c of [
		"find . -name x -delete", "find . -exec cat {} ;", "find . -execdir ls ;", "find . -fprint /tmp/x",
		"rg --pre ./evil x", "sort -o out in", "sort --compress-program=sh x", "journalctl --vacuum-time=1d",
		"git -c core.pager=sh log", "git branch newbranch", "git branch -D x", "git push",
		"git commit -m x", "git checkout main", "systemctl start x", "systemctl -H host status x",
		"ip addr add 1.2.3.4/24 dev eth0", "ip link set eth0 down", "nix build .#x", "nix run nixpkgs#hello",
		"nix eval --option allow-unsafe-native-code-during-evaluation true .#x", "zpool scrub tank",
		"zfs snapshot tank@x", "podman exec x sh", "podman run alpine", "nvidia-smi -pl 300",
		"nvidia-smi -r", "podman --remote ps",
	]) assert.notEqual(kind(c), "sandbox", c);
});

test("destructive patterns", () => {
	for (const c of [
		"rm -rf /tmp/x", "dd if=/dev/zero of=/dev/sda", "mkfs.ext4 /dev/sdb1", "wipefs -a /dev/sdb",
		"shred f", "truncate -s0 f", "zfs destroy tank/x", "zfs rollback tank/x@s", "zpool destroy tank",
		"zpool remove tank sdb", "zpool offline tank sdb", "zpool detach tank sdb", "nixos-rebuild switch",
		"systemctl stop llama-swap", "systemctl restart strata", "systemctl --now disable x", "systemctl mask x",
		"reboot", "poweroff", "kill 1234", "pkill -f llama", "git push --force origin main", "git push -f",
		"git push origin +main", "git push origin :old", "git push --delete origin x", "git reset --hard HEAD~1",
		"git clean -fdx", "chmod -R 777 /x", "chown -R a:b /x", "sudo ls", "podman rm -f x", "podman stop x",
		"virsh destroy vast", "virsh undefine vast", "nft add rule inet filter input drop", "nft flush ruleset",
		"iptables -A INPUT -j DROP", "iptables -F", "ls && rm x", "echo rm | xargs rm",
		'bash -c "rm -rf /x"', "find / -delete", "/run/current-system/sw/bin/rm x",
		"ssh root@cdssrv01 reboot", "cat x | sudo tee /etc/y",
	]) assert.equal(kind(c), "destructive", c);
});

test("read-only commands mentioning destructive words are not flagged", () => {
	assert.equal(kind("grep -rn 'rm -rf' modules/"), "sandbox");
	assert.equal(kind("git log --grep=reboot"), "sandbox");
	assert.equal(kind("journalctl -u sshd --grep kill"), "sandbox");
});

test("non-destructive unknown commands → confirm", () => {
	for (const c of ["curl http://localhost:9000/v1/models", "nft list ruleset", "systemctl start x", "vim x", ""])
		assert.equal(kind(c), "confirm", c);
});

test("bwrap wrapper keeps the command intact", () => {
	const s = bwrapArgv("grep 'a b' x | wc -l", { bwrap: "/nix/store/b/bin/bwrap", bash: "/nix/store/s/bin/bash", devices: ["/dev/zfs"] });
	assert.match(s, /^\/nix\/store\/b\/bin\/bwrap --ro-bind \/ \/ --dev \/dev --dev-bind-try \/dev\/zfs \/dev\/zfs --proc \/proc --tmpfs \/tmp/);
	assert.ok(s.endsWith(`/nix/store/s/bin/bash -c 'grep '\\''a b'\\'' x | wc -l'`), s);
});

test("edit root containment", () => {
	const r = "/home/cristian/cristids/nixos_llm_srv";
	assert.ok(isInsideRoot(`${r}/modules/x.nix`, r));
	assert.ok(!isInsideRoot(`${r}-evil/x`, r));
	assert.ok(!isInsideRoot(`${r}/.git/hooks/pre-push`, r));
	assert.ok(!isInsideRoot("/etc/passwd", r));
});

test("project root follows the start folder, never $HOME or above", async () => {
	const { projectRoot } = await import("./classifier.ts");
	const home = "/home/cristian";
	const git = (dirs: string[]) => (d: string) => dirs.includes(d);
	// git work tree: its top level, from any subfolder
	assert.equal(projectRoot(`${home}/haos/sub/dir`, home, git([`${home}/haos`])), `${home}/haos`);
	assert.equal(projectRoot(`${home}/haos/`, home, git([`${home}/haos`])), `${home}/haos`);
	// no git: the folder itself
	assert.equal(projectRoot(`${home}/scratch/x`, home, git([])), `${home}/scratch/x`);
	// $HOME, / and ancestors of $HOME never become an auto-edit root
	assert.equal(projectRoot(home, home, git([])), null);
	assert.equal(projectRoot(`${home}/`, home, git([])), null);
	assert.equal(projectRoot("/", home, git([])), null);
	assert.equal(projectRoot("/home", home, git([])), null);
	assert.equal(projectRoot(`${home}/notes`, home, git([home])), null); // dotfiles repo in $HOME
	assert.equal(projectRoot(`${home}/a`, home, git(["/"])), null);
	// outside $HOME is fine
	assert.equal(projectRoot("/srv/app/src", home, git(["/srv/app"])), "/srv/app");
});

test("read-only MCP tools are auto-allowed by exact name", async () => {
	const { toolNeedsConfirm } = await import("./classifier.ts");
	for (const [t, i] of [
		["read", { path: "/etc/os-release" }],
		["grep", {}],
		["mcp__searxng__searxng_web_search", { query: "nixos" }],
		["mcp__searxng__web_url_read", { url: "https://nixos.org/manual" }],
		["mcp__context7__resolve_library_id", { libraryName: "next.js" }],
		["mcp__context7__query_docs", { libraryId: "/vercel/next.js", query: "x" }],
		["mcp__nixos__nix", { action: "search", query: "openssh" }],
		["mcp__nixos__nix", { action: "info", query: "services.openssh.enable", type: "option" }],
		["mcp__nixos__nix", { query: "hello" }],
		["mcp__nixos__nix_versions", { package: "python" }],
		["mcp__erpnext__list_environments", {}],
		["mcp__erpnext__get_doctype_meta", { doctype: "Customer" }],
		["mcp__erpnext__list_docs", { doctype: "Customer" }],
		["mcp__erpnext__get_doc", { doctype: "Customer", name: "X" }],
		["mcp__erpnext__run_report", { report_name: "General Ledger" }],
	] as const) assert.equal(toolNeedsConfirm(t, i), null, t);
});

test("other MCP tools, lookalikes and unknown tools ask", async () => {
	const { toolNeedsConfirm } = await import("./classifier.ts");
	for (const [t, i] of [
		["mcp__searxng__searxng_search_suggestions", {}],
		["mcp__searxng__searxng_instance_info", {}],
		["mcp__context7__submit_docs_feedback", {}],
		["mcp__nixos__nix", { action: "flake-inputs", query: "/etc/nixos" }],
		["mcp__nixos__nix", { action: "FLAKE-INPUTS" }],
		["mcp__evil__searxng_web_search", {}],
		["mcp__searxng__searxng_web_search_x", {}],
		["mcp__nixos__nix_versions_2", {}],
		["mcp__erpnext__create_doc", { doctype: "Customer", data: {} }],
		["mcp__erpnext__update_doc", { doctype: "Customer", name: "X", data: {} }],
		["mcp__erpnext__call_method", { method: "frappe.client.get_count" }],
		["list_mcp_resources", {}],
		["read_mcp_resource", {}],
		["codemode", {}],
		["tool_search", {}],
		["bash", { command: "ls" }],
		["write", {}],
		["something_new", {}],
	] as const) assert.notEqual(toolNeedsConfirm(t, i), null, t);
});

test("web_url_read: only public http(s) URLs are auto-allowed", async () => {
	const { toolNeedsConfirm } = await import("./classifier.ts");
	const r = (url: unknown) => toolNeedsConfirm("mcp__searxng__web_url_read", { url });
	for (const u of ["https://github.com/NixOS/nixpkgs", "http://example.com/a?b=c"]) assert.equal(r(u), null, u);
	for (const u of [
		"http://127.0.0.1:8080/", "http://localhost:9000/v1/models", "http://cdssrv01/", "http://192.168.40.28:8080",
		"http://[::1]:80/", "http://2130706433/", "http://0x7f000001/", "file:///etc/shadow",
		"https://user:pw@example.com/", "http://router.lan/", "http://x.local/", "not a url", undefined,
	]) assert.notEqual(r(u), null, String(u));
});

test("plain git push is not destructive (goes to the reviewer)", () => {
	assert.equal(kind("git push"), "confirm");
	assert.equal(kind("git push origin main"), "confirm");
});

test("host-only tools skip the project sandbox", async () => {
	const { needsHost } = await import("./classifier.ts");
	for (const c of ["ssh root@h uptime", "scp a h:/b", "git push", "git -C x fetch", "podman ps -a", "systemctl --user restart x", "nix profile install nixpkgs#jq", "make && ssh h ls"])
		assert.notEqual(needsHost(c), null, c);
	for (const c of ["npm test", "cargo build", "git commit -m x", "git add .", "nix build .#x", "make", "pytest -q"])
		assert.equal(needsHost(c), null, c);
});

test("project sandbox: project writable, .git hooks/config read-only after it", async () => {
	const { bwrapProjectArgv } = await import("./classifier.ts");
	const s = bwrapProjectArgv("npm test", {
		bwrap: "/b/bwrap", bash: "/b/bash", devices: [], root: "/home/u/p", cache: "/var/lib/pi-admin/cache/abc",
		roBinds: ["/home/u/p/.git/hooks", "/home/u/p/.git/config"], uid: 1000,
	});
	assert.ok(s.indexOf("--bind /home/u/p /home/u/p") < s.indexOf("--ro-bind /home/u/p/.git/hooks"), s);
	assert.match(s, /--tmpfs \/run\/user\/1000 /);
	assert.match(s, /--unsetenv SSH_AUTH_SOCK /);
	assert.match(s, /--setenv npm_config_cache \/var\/lib\/pi-admin\/cache\/abc\/npm /);
	assert.ok(s.endsWith("/b/bash -c 'npm test'"), s);
});

test("rule keys", async () => {
	const { ruleKey, ruleKeysFor, matchesRules } = await import("./classifier.ts");
	const k = (c: string) => ruleKey(c.split(" "));
	assert.equal(k("ssh root@192.168.40.10 ha core check"), "ssh root@192.168.40.10");
	assert.equal(k("ssh -p 22 -i key root@h ls"), "ssh root@h");
	assert.equal(k("ssh -o ProxyCommand=sh root@h"), null);
	assert.equal(k("ssh -F cfg h"), null);
	assert.equal(k("scp a root@h:/x"), "scp root@h");
	assert.equal(k("scp -S prog a h:/x"), null);
	assert.equal(k("rsync -a a h:/x"), "rsync h");
	assert.equal(k("rsync -e sh a h:/x"), null);
	assert.equal(k("make deploy"), "make deploy");
	assert.equal(k("./run.sh"), null);
	assert.equal(k("FOO=1 make"), null);
	assert.deepEqual(ruleKeysFor("ls && ssh root@h uptime"), ["ssh root@h"]);
	assert.equal(ruleKeysFor("ssh h $(cat x)"), null);
	const rules = ["ssh root@h"];
	assert.ok(matchesRules("ssh root@h uptime", rules));
	assert.ok(matchesRules("ssh -p 22 root@h ls; cat x", rules));
	assert.ok(!matchesRules("ssh root@other uptime", rules));
	assert.ok(!matchesRules("ssh root@h uptime && curl x", rules));
	assert.ok(!matchesRules("ssh -o ProxyCommand=x root@h", rules));
});

test("reviewer choice and verdict parsing", async () => {
	const { pickReviewer, parseVerdict } = await import("./classifier.ts");
	assert.equal(pickReviewer(["glm-5.3", "kimi-k3"], "deepseek-v4.1-flash-1017"), "glm-5.3");
	assert.equal(pickReviewer(["glm-5.3", "kimi-k3"], "glm-5.3"), "kimi-k3");
	assert.equal(pickReviewer(["glm-5.3"], "glm-5.3"), null);
	assert.deepEqual(parseVerdict('{"decision":"allow","reason":"ok"}'), { decision: "allow", reason: "ok" });
	assert.equal(parseVerdict('sure ```json\n{"decision":"allow","reason":"x"}\n```').decision, "allow");
	assert.equal(parseVerdict('{"decision":"ALLOW"}').decision, "ask");
	assert.equal(parseVerdict("allow").decision, "ask");
	assert.equal(parseVerdict("").decision, "ask");
});
