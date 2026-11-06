import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location("pi_task", Path(__file__).with_name("pi-task.py"))
pi_task = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pi_task
SPEC.loader.exec_module(pi_task)


class PiTaskPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bound_sockets = []
        self.old_home = pi_task.HOME
        self.old_state_home = os.environ.get("XDG_STATE_HOME")
        pi_task.HOME = Path(self.temp.name)
        os.environ["XDG_STATE_HOME"] = str(pi_task.HOME / "state")
        self.repo = pi_task.HOME / "scratch/repo"
        self.repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "test@example.invalid"], check=True)
        (self.repo / "allowed.txt").write_text("old\n")
        (self.repo / "other.txt").write_text("stable\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "base"], check=True)

    def tearDown(self):
        for bound in self.bound_sockets:
            bound.close()
        pi_task.HOME = self.old_home
        if self.old_state_home is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = self.old_state_home
        self.temp.cleanup()

    def bind_socket(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        bound = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        bound.bind(str(path))
        self.bound_sockets.append(bound)
        return path

    def test_allowed_change_passes(self):
        before = pi_task.snapshot(self.repo)
        (self.repo / "allowed.txt").write_text("new\n")
        self.assertEqual([], pi_task.policy_violations(before, pi_task.snapshot(self.repo), {"allowed.txt"}))

    def test_out_of_scope_tracked_change_fails(self):
        before = pi_task.snapshot(self.repo)
        (self.repo / "other.txt").write_text("changed\n")
        self.assertIn("out-of-scope path changed: other.txt",
                      pi_task.policy_violations(before, pi_task.snapshot(self.repo), {"allowed.txt"}))

    def test_out_of_scope_untracked_file_fails(self):
        before = pi_task.snapshot(self.repo)
        (self.repo / "surprise.txt").write_text("new\n")
        self.assertIn("out-of-scope path changed: surprise.txt",
                      pi_task.policy_violations(before, pi_task.snapshot(self.repo), {"allowed.txt"}))

    def test_preexisting_unrelated_dirty_file_is_preserved(self):
        (self.repo / "other.txt").write_text("user work\n")
        before = pi_task.snapshot(self.repo)
        (self.repo / "allowed.txt").write_text("agent work\n")
        self.assertEqual([], pi_task.policy_violations(
            before, pi_task.snapshot(self.repo), {"allowed.txt"}))

    def test_head_movement_fails(self):
        before = pi_task.snapshot(self.repo)
        (self.repo / "allowed.txt").write_text("new\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "allowed.txt"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "bad"], check=True)
        problems = pi_task.policy_violations(before, pi_task.snapshot(self.repo), {"allowed.txt"})
        self.assertTrue(any(problem.startswith("HEAD moved:") for problem in problems))
        self.assertIn("the Git index changed", problems)

    def test_index_change_fails(self):
        before = pi_task.snapshot(self.repo)
        (self.repo / "allowed.txt").write_text("new\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "allowed.txt"], check=True)
        self.assertIn("the Git index changed",
                      pi_task.policy_violations(before, pi_task.snapshot(self.repo), {"allowed.txt"}))

    def test_allowlist_rejects_git_and_escape(self):
        for value in (".git/config", "../outside", "/tmp/outside"):
            with self.subTest(value=value), self.assertRaises(pi_task.PolicyError):
                pi_task.validate_allowed(self.repo, [value])

    def test_allowlist_rejects_symlink_escape(self):
        (self.repo / "escape").symlink_to("/tmp")
        with self.assertRaises(pi_task.PolicyError):
            pi_task.validate_allowed(self.repo, ["escape/file"])

    def test_normal_clone_and_linked_worktree_are_supported(self):
        self.assertEqual(self.repo, pi_task.validate_repo(str(self.repo)))
        worktree = pi_task.HOME / "scratch/linked"
        subprocess.run(["git", "-C", str(self.repo), "worktree", "add", "-qb",
                        "linked-test", str(worktree)], check=True)
        self.assertEqual(worktree, pi_task.validate_repo(str(worktree)))
        layout = pi_task.git_layout(worktree)
        self.assertTrue(layout.dotgit.is_file())
        self.assertEqual((self.repo / ".git").resolve(), layout.common)
        self.assertTrue(layout.gitdir.is_relative_to(layout.common))
        mounts = pi_task.git_mount_args(layout)
        self.assertIn(str(layout.dotgit), mounts)
        self.assertIn(str(layout.common), mounts)
        self.assertEqual(2, mounts.count("--ro-bind"))
        local_mounts = pi_task.project_mount_args(worktree, layout)
        self.assertIn(str(layout.dotgit), local_mounts)
        self.assertIn(str(layout.common), local_mounts)

    def test_empty_nested_gitlink_does_not_absorb_linked_worktree_status(self):
        gitlink = ".claude/worktrees/dazzling-shaw"
        head = subprocess.check_output([
            "git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run([
            "git", "-C", str(self.repo), "update-index", "--add", "--cacheinfo",
            f"160000,{head},{gitlink}"], check=True)
        subprocess.run([
            "git", "-C", str(self.repo), "commit", "-qm", "track empty nested gitlink"],
            check=True)

        worktree = pi_task.HOME / "scratch/gitlink-worker"
        subprocess.run([
            "git", "-C", str(self.repo), "worktree", "add", "-qb", "gitlink-worker",
            str(worktree)], check=True)
        (worktree / gitlink).mkdir(parents=True, exist_ok=True)
        before = pi_task.snapshot(worktree)
        allowed = "qa/leave-rebuild/test_n05_owned_cleanup_offline.py"
        (worktree / allowed).parent.mkdir(parents=True)
        (worktree / allowed).write_text("def test_candidate():\n    pass\n")
        after = pi_task.snapshot(worktree)

        self.assertEqual(before["files"][gitlink], after["files"][gitlink])
        self.assertEqual([], pi_task.policy_violations(before, after, {allowed}))

        (worktree / gitlink / "unexpected.txt").write_text("changed\n")
        changed = pi_task.snapshot(worktree)
        self.assertIn(
            f"out-of-scope path changed: {gitlink}",
            pi_task.policy_violations(after, changed, {allowed}),
        )

    def test_model_allowlist_has_no_fallback(self):
        self.assertEqual("local-qwen", pi_task.parse_args([
            "--success", "true", "brief",
        ]).model)
        self.assertEqual("melious-dsf", pi_task.parse_args([
            "--model", "melious-dsf", "--success", "true", "brief",
        ]).model)
        with self.assertRaises(SystemExit):
            pi_task.parse_args(["--model", "melious/deepseek-v4.1-flash",
                                "--success", "true", "brief"])
        with self.assertRaises(SystemExit):
            pi_task.parse_args(["--model", "melious-dsf", "--read", "src",
                                "--success", "true", "brief"])

    def test_worker_id_is_explicitly_scoped_to_dsf(self):
        dsf = pi_task.parse_args([
            "--model", "melious-dsf", "--worker-id", "review-17",
            "--success", "true", "brief",
        ])
        self.assertEqual("review-17", dsf.worker_id)

    def test_control_call_uses_stable_system_path(self):
        completed = subprocess.CompletedProcess([], 0, stdout='{"state":"active"}\n',
                                                stderr="")
        with mock.patch.object(pi_task, "run", return_value=completed) as called:
            self.assertEqual({"state": "active"}, pi_task.control_call("status"))
        self.assertEqual(pi_task.PI_TASK_CONTROL, called.call_args.args[0][0])

    def test_model_config_exposes_exactly_two_production_models(self):
        path = pi_task.HOME / "models.json"
        path.write_text(json.dumps({"providers": {
            "homelab": {"models": [{"id": "strata/qwen3.8-flash"}]},
            "melious": {"models": [{"id": "deepseek-v4.1-flash-1017"}]},
        }}))
        pi_task.validate_model_config(path)
        data = json.loads(path.read_text())
        data["providers"]["melious"]["models"].append({"id": "fallback"})
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(pi_task.PolicyError, "exactly"):
            pi_task.validate_model_config(path)

    def test_local_qwen_has_one_global_exclusive_lock(self):
        runtime = pi_task.HOME / "runtime"
        runtime.mkdir()
        qwen = pi_task.acquire_exclusive_lock(runtime, "local-qwen")
        with self.assertRaisesRegex(pi_task.PolicyError, "already allocated"):
            pi_task.acquire_exclusive_lock(runtime, "local-qwen")
        qwen.close()

    def test_dsf_models_config_is_worker_scoped_without_fallback(self):
        source = pi_task.HOME / "source-models.json"
        source.write_text(json.dumps({"providers": {
            "homelab": {"baseUrl": "http://local/v1", "models": [
                {"id": "strata/qwen3.8-flash"}]},
            "melious": {"baseUrl": "http://127.0.0.1:5098/w/unregistered/v1",
                         "models": [{"id": "deepseek-v4.1-flash-1017"}]},
        }}))
        stage = pi_task.HOME / "stage-models"
        stage.mkdir()
        rendered = pi_task.write_run_models(stage, source, "melious-dsf", "worker-9")
        config = json.loads(rendered.read_text())
        self.assertEqual("http://127.0.0.1:5098/w/worker-9/v1",
                         config["providers"]["melious"]["baseUrl"])
        self.assertEqual(2, sum(len(value["models"])
                                for value in config["providers"].values()))

    def test_trusted_providers_share_full_git_tool_and_mcp_capabilities(self):
        runtime = pi_task.HOME / "runtime"
        tuios = self.bind_socket(runtime / "tuios/tuios.sock")
        erpnext = self.bind_socket(runtime / "pi-task-erpnext-test.sock")
        agent = pi_task.HOME / ".pi/agent"
        (agent / "extensions").mkdir(parents=True)
        (agent / "settings.json").write_text("{}\n")
        (agent / "extensions/tuios-agent-state.ts").write_text("// coordination integration\n")
        stage = pi_task.HOME / "channel-stage"
        stage.mkdir()
        models = stage / "models.json"
        models.write_text("{}\n")
        layout = pi_task.git_layout(self.repo)
        old_env = {key: os.environ.get(key) for key in (
            "XDG_RUNTIME_DIR", "TUIOS_SOCKET", "TUIOS_PANE_ID", "TUIOS_PANE_TOKEN")}
        os.environ.update({
            "XDG_RUNTIME_DIR": str(runtime), "TUIOS_SOCKET": str(tuios),
            "TUIOS_PANE_ID": "pane-test", "TUIOS_PANE_TOKEN": "token-test",
        })
        try:
            qwen_mcp, qwen_sockets = pi_task.write_mcp(stage, "test", "qwen-mcp.json")
            dsf_mcp, dsf_sockets = pi_task.write_mcp(stage, "test", "dsf-mcp.json")
            self.assertEqual(qwen_mcp.read_text(), dsf_mcp.read_text())
            config = json.loads(qwen_mcp.read_text())["mcpServers"]
            self.assertEqual({"context7", "tuios", "web", "erpnext"}, set(config))
            self.assertEqual({"searxng_*": "direct", "web_url_read": "direct"},
                             config["web"]["toolExposure"])
            self.assertEqual([erpnext], qwen_sockets)
            self.assertEqual(qwen_sockets, dsf_sockets)

            qwen_args = pi_task.sandbox_args(
                self.repo, layout, models, qwen_mcp, qwen_sockets)
            dsf_args = pi_task.sandbox_args(
                self.repo, layout, models, qwen_mcp, dsf_sockets)
            self.assertEqual(qwen_args, dsf_args)
            serialized = "\n".join(qwen_args)
            self.assertNotIn("--unshare-net", qwen_args)
            for required in (str(tuios), str(erpnext), str(layout.dotgit),
                             "TUIOS_SOCKET", "TUIOS_PANE_TOKEN",
                             "tuios-agent-state.ts", "/etc", "/bin", "/usr",
                             "SSL_CERT_FILE", "NIX_SSL_CERT_FILE"):
                self.assertIn(required, serialized)
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_full_git_history_is_visible_in_normal_and_linked_worktrees(self):
        (self.repo / "historical.txt").write_text("HISTORY-CANARY\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "historical.txt"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "history canary"],
                       check=True)
        historical_blob = subprocess.check_output([
            "git", "-C", str(self.repo), "rev-parse", "HEAD:historical.txt"], text=True).strip()
        subprocess.run(["git", "-C", str(self.repo), "rm", "-q", "historical.txt"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "delete canary"],
                       check=True)
        linked = pi_task.HOME / "scratch/capability-linked"
        subprocess.run(["git", "-C", str(self.repo), "worktree", "add", "-qb",
                        "capability-linked", str(linked)], check=True)
        git_bin = str(Path(shutil.which("git")).resolve())
        for label, checkout in (("normal", self.repo), ("linked", linked)):
            with self.subTest(checkout=label):
                layout = pi_task.git_layout(checkout)
                mounts = pi_task.project_mount_args(checkout, layout)
                result = subprocess.run([
                    shutil.which("bwrap"), "--die-with-parent", "--unshare-pid",
                    "--unshare-ipc", "--unshare-uts", "--new-session", "--clearenv",
                    "--ro-bind", "/nix/store", "/nix/store", "--proc", "/proc",
                    "--dev", "/dev", "--tmpfs", "/tmp", *mounts,
                    "--chdir", str(checkout), "--", git_bin, "cat-file", "-p",
                    historical_blob,
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual("HISTORY-CANARY\n", result.stdout)

    def test_accounting_refuses_observed_model_fallback(self):
        events = pi_task.HOME / "events.jsonl"
        events.write_text(json.dumps({
            "type": "message_end",
            "message": {"role": "assistant", "model": "deepseek-v4.1-flash",
                        "usage": {"input": 10, "output": 2, "cost": {"total": 0.1}}},
        }) + "\n")
        with self.assertRaisesRegex(pi_task.PolicyError, "model identity mismatch"):
            pi_task.accounting(events, "melious-dsf", 1.0, 0)

    def test_web_health_is_optional_unless_explicitly_required(self):
        with mock.patch.object(pi_task, "urlopen", side_effect=OSError("offline")):
            available, detail = pi_task.web_admission(False)
        self.assertFalse(available)
        self.assertIn("offline", detail)
        with mock.patch.object(pi_task, "urlopen", side_effect=OSError("offline")):
            with self.assertRaisesRegex(pi_task.PolicyError, "SearXNG MCP backend unavailable"):
                pi_task.web_admission(True)
        optional = pi_task.parse_args(["--success", "true", "brief"])
        required = pi_task.parse_args(["--require-web", "--success", "true", "brief"])
        self.assertFalse(optional.require_web)
        self.assertTrue(required.require_web)

    def test_worker_receives_acceptance_and_autonomous_repair_instructions(self):
        rules = pi_task.worker_rules(
            "melious-dsf", {"src/feature.py", "tests/test_feature.py"},
            "pytest -q tests/test_feature.py",
        )
        self.assertIn("autonomous bounded implementation worker", rules)
        self.assertIn("iterate test/fix until it passes", rules)
        self.assertIn("pytest -q tests/test_feature.py", rules)
        self.assertIn("wrapper will independently run it again", rules)
        self.assertNotIn("wrapper, not you", rules.lower())
        self.assertIn("src/feature.py", rules)

    def test_attempt_tracking_is_bounded_to_three(self):
        self.assertEqual(3, pi_task.parse_args([
            "--attempt", "3", "--success", "true", "brief"]).attempt)
        with self.assertRaises(SystemExit):
            pi_task.parse_args(["--attempt", "4", "--success", "true", "brief"])

    def test_third_failure_is_audited_for_takeover(self):
        audit = pi_task.AuditLogger("run-fail", "local-qwen", "local-qwen")
        pi_task.report_failure(3, "test", audit)
        record = json.loads(audit.path.read_text().splitlines()[-1])
        self.assertEqual("run-failure", record["kind"])
        self.assertEqual("test", record["classification"])
        self.assertTrue(record["takeover_required"])

    def test_network_metadata_records_destinations_without_payloads(self):
        ipv4 = pi_task.network_metadata(
            '9 connect(3, {sa_family=AF_INET, sin_port=htons(8080), '
            'sin_addr=inet_addr("192.168.40.28")}, 16) = 0')
        self.assertEqual("searxng", ipv4["category"])
        self.assertEqual("192.168.40.28:8080", ipv4["destination"])
        public = pi_task.network_metadata(
            '9 connect(3, {sa_family=AF_INET, sin_port=htons(443), '
            'sin_addr=inet_addr("1.1.1.1")}, 16) = -1 EINPROGRESS')
        self.assertEqual("public-internet", public["category"])
        unix = pi_task.network_metadata(
            '9 connect(3, {sa_family=AF_UNIX, sun_path="/run/user/1000/tuios.sock"}, 32) = 0')
        self.assertEqual("tuios-socket", unix["category"])
        self.assertNotIn("sun_path", json.dumps(unix))

    def test_live_monitor_persists_sanitized_tool_usage_and_network_metadata(self):
        audit = pi_task.AuditLogger("run-test", "local-qwen", "local-qwen")
        events = pi_task.HOME / "events.jsonl"
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        secret = "PROMPT-SOURCE-SECRET"
        script = (
            "import json,socket,time;"
            "print(json.dumps({'type':'tool_execution_start','toolName':'bash',"
            f"'toolCallId':'call-1','args':{{'command':'{secret}'}}}}),flush=True);"
            f"s=socket.socket();s.connect(('127.0.0.1',{port}));s.close();time.sleep(0.05);"
            "print(json.dumps({'type':'tool_execution_end','toolName':'bash',"
            "'toolCallId':'call-1','isError':False}),flush=True);"
            "print(json.dumps({'type':'message_end','message':{'role':'assistant',"
            "'model':'strata/qwen3.8-flash','content':'PRIVATE SOURCE',"
            "'usage':{'input':3,'output':2,'cacheRead':1,'cost':{'total':0}}}}),flush=True)"
        )
        try:
            rc = pi_task.monitored_run([sys.executable, "-c", script], events, audit)
        finally:
            listener.close()
        self.assertEqual(0, rc)
        records = [json.loads(line) for line in audit.path.read_text().splitlines()]
        self.assertEqual(
            ["tool-start", "network-connect", "tool-end", "usage"],
            [record["kind"] for record in records],
        )
        persisted = audit.path.read_text()
        self.assertNotIn(secret, persisted)
        self.assertNotIn("PRIVATE SOURCE", persisted)
        self.assertIn(f"127.0.0.1:{port}", persisted)
        self.assertIn("loopback", persisted)
        self.assertIn("strata/qwen3.8-flash", persisted)


if __name__ == "__main__":
    unittest.main()
