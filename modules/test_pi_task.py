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

    def test_worker_scope_disables_swap(self):
        runtime = pi_task.HOME / "runtime"
        self.bind_socket(runtime / "bus")
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime), "PI_TASK_SCOPED": "0"}), \
                mock.patch.object(pi_task.os, "execvpe") as execute:
            pi_task.ensure_scoped(["--success", "true", "brief"])
            argv = execute.call_args.args[1]
            self.assertIn("MemoryMax=6G", argv)
            self.assertIn("MemorySwapMax=0", argv)

    def test_acceptance_defaults_and_explicit_dependencies_leave_candidate_tests_editable(self):
        slot, app = self.prepare_w()
        (slot / "config").mkdir()
        gate = slot / "config/native-acceptance-gate.py"
        manifest = slot / "config/acceptance-manifest.json"
        gate.write_text("raise SystemExit(17)\n")
        manifest.write_text("{}")
        packet = app / "openspec/changes/check"
        packet.mkdir(parents=True)
        success = packet / "success.sh"
        success.write_text("exit 17\n")
        candidate = app / "test_candidate.py"
        candidate.write_text("# worker-owned test\n")
        dependency = app / "checker.json"
        dependency.write_text("{}")
        paths = pi_task.acceptance_files(app, slot, ["checker.json"], {"test_candidate.py"})
        self.assertEqual({gate, manifest, success, dependency, slot / "toolchain.sh"}, set(paths))
        self.assertNotIn(candidate, paths)
        with self.assertRaises(pi_task.PolicyError):
            pi_task.acceptance_files(app, slot, ["checker.json"], {"checker.json"})
        with self.assertRaises(pi_task.PolicyError):
            pi_task.acceptance_files(app, slot, [str(self.repo / "allowed.txt")], set())
        with self.assertRaises(pi_task.PolicyError):
            pi_task.acceptance_files(app, slot, ["missing.py"], set())
        alias = app / "alias.json"
        alias.symlink_to(dependency)
        with self.assertRaises(pi_task.PolicyError):
            pi_task.acceptance_files(app, slot, ["alias.json"], set())

    def test_acceptance_snapshot_survives_alias_writes_and_detects_replacement(self):
        target = self.repo / "allowed.txt"
        alias = self.repo / "hardlink.txt"
        os.link(target, alias)
        stage = pi_task.HOME / "stage"
        stage.mkdir()
        frozen = pi_task.freeze_acceptance(stage, [target])
        self.assertEqual([], pi_task.acceptance_violations(frozen))
        alias.write_text("tampered\n")
        self.assertEqual("old\n", frozen[0][0].read_text())
        self.assertTrue(pi_task.acceptance_violations(frozen))
        target.unlink()
        target.symlink_to(alias)
        self.assertTrue(pi_task.acceptance_violations(frozen))

    def test_readonly_acceptance_blocks_write_unlink_replace_but_not_candidate_work(self):
        stage = pi_task.HOME / "stage"
        stage.mkdir()
        target = self.repo / "other.txt"
        frozen = pi_task.freeze_acceptance(stage, [target])
        script = '''
import errno, os
from pathlib import Path
target = Path("other.txt")
for action in (lambda: target.write_text("cheat"), lambda: target.unlink(),
               lambda: os.replace("allowed.txt", target)):
    try:
        action()
    except OSError as exc:
        assert exc.errno in (errno.EROFS, errno.EBUSY, errno.EACCES), exc
    else:
        raise AssertionError("acceptance was writable")
assert target.read_text() == "stable\\n"
Path("allowed.txt").write_text("worker change\\n")
Path("new_test.py").write_text("# candidate test\\n")
'''
        command = [shutil.which("bwrap"), "--die-with-parent", "--unshare-pid",
                   "--ro-bind", "/nix/store", "/nix/store", "--proc", "/proc",
                   "--dev", "/dev", "--tmpfs", "/tmp",
                   "--bind", str(self.repo), str(self.repo),
                   "--ro-bind", str(frozen[0][0]), str(target),
                   "--chdir", str(self.repo), "--", str(Path(sys.executable).resolve()), "-c", script]
        for phase in ("worker", "success"):
            with self.subTest(phase=phase):
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], pi_task.acceptance_violations(frozen))
        self.assertEqual("worker change\n", (self.repo / "allowed.txt").read_text())

    def test_live_host_attestation_readonly_and_runtime_paths_writable(self):
        slot, app = self.prepare_w()
        config = slot / "config"
        (config / "pids").mkdir(parents=True)
        (config / "web-reload").touch()
        (config / "bench_build.lock").touch()
        script = '''
import errno,sys
from pathlib import Path
print("ready", flush=True)
sys.stdin.readline()
p = Path("config/root-runtime-attestation.json")
assert p.read_text() == "trusted update"
for action in (lambda: p.write_text("forged"), lambda: p.unlink(),
               lambda: Path("config/new-approval.json").write_text("forged")):
    try:
        action()
    except OSError as exc:
        assert exc.errno in (errno.EROFS, errno.EBUSY, errno.EACCES), exc
    else:
        raise AssertionError("host control file writable")
Path("config/web-reload").touch()
Path("config/bench_build.lock").write_text("lock")
Path("config/pids/probe").write_text("pid")
'''
        command = [shutil.which("bwrap"), "--die-with-parent", "--unshare-pid",
                   "--ro-bind", "/nix/store", "/nix/store", "--proc", "/proc",
                   "--dev", "/dev", "--tmpfs", "/tmp",
                   "--bind", str(slot), str(slot),
                   *pi_task.reviewer_mount_args(app, slot),
                   "--chdir", str(slot), "--", str(Path(sys.executable).resolve()), "-c", script]
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as process:
            self.assertEqual("ready\n", process.stdout.readline())
            pending = config / "attestation.tmp"
            pending.write_text("trusted update")
            os.replace(pending, config / "root-runtime-attestation.json")
            _, stderr = process.communicate("continue\n", timeout=10)
            self.assertEqual(0, process.returncode, stderr)
        self.assertEqual("trusted update", (config / "root-runtime-attestation.json").read_text())

    def test_pane_reporting_targets_only_own_pane_and_is_bounded(self):
        with mock.patch.dict(os.environ, {
            "TUIOS_PANE_ID": "worker-pane", "TUIOS_SESSION": "workers",
            "TUIOS_SOCKET": "/run/user/1000/tuios/tuios.sock",
        }), mock.patch.object(pi_task.subprocess, "run") as call:
            call.return_value.returncode = 0
            for state in ("working", "done", "errored"):
                self.assertTrue(pi_task.report_pane(state, "fixed status"))
                argv = call.call_args.args[0]
                self.assertEqual("worker-pane", argv[argv.index("--window") + 1])
                self.assertEqual("workers", argv[argv.index("--session") + 1])
                self.assertEqual("pi", argv[argv.index("--harness") + 1])
                self.assertEqual(state, argv[2])
                self.assertEqual(2, call.call_args.kwargs["timeout"])
            call.return_value.returncode = 1
            self.assertFalse(pi_task.report_pane("working", "status"))
            for error in (OSError("offline"), subprocess.TimeoutExpired("tuios", 2)):
                call.side_effect = error
                self.assertFalse(pi_task.report_pane("working", "status"))

    def test_pane_reporting_never_falls_back_to_focused_pane(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(pi_task.subprocess, "run") as call:
            self.assertFalse(pi_task.report_pane("working", "status"))
            call.assert_not_called()

    def prepare_w(self):
        root = pi_task.HOME / "pi-envs"
        slot = root / "w1"
        app = slot / "apps/roster"
        app.parent.mkdir(parents=True)
        shutil.copytree(self.repo, app)
        (slot / "toolchain.sh").write_text("export PATH=/w-toolchain:$PATH\n")
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(pi_task, "W_ROOT", root).start()
        return slot, app

    def test_w_checkout_requires_exact_matching_slot_and_local_git(self):
        slot, app = self.prepare_w()
        self.assertEqual(app, pi_task.validate_repo(str(app)))
        layout = pi_task.git_layout(app)
        self.assertEqual(slot, pi_task.validate_w_assignment(app, layout, "w1"))
        for bench in (None, "w2", "test1"):
            with self.assertRaises(pi_task.PolicyError):
                pi_task.validate_w_assignment(app, layout, bench)
        for name in ("w0", "w7", ".control", "w1-other"):
            self.assertIsNone(pi_task.w_slot(slot.parent / name))
        escaped = pi_task.GitLayout(layout.dotgit, self.repo / ".git", self.repo / ".git")
        with self.assertRaises(pi_task.PolicyError):
            pi_task.validate_w_assignment(app, escaped, "w1")

    def test_w_lease_conflicts_with_lifecycle_and_other_worker(self):
        slot, app = self.prepare_w()
        with pi_task.acquire_w_lease(slot):
            with self.assertRaises(pi_task.PolicyError):
                pi_task.acquire_w_lease(slot)
            with pi_task.acquire_w_lease(slot.parent / "w2"):
                pass
        with pi_task.acquire_w_lease(slot):
            pass

    def test_other_app_source_changes_are_guarded_but_runtime_state_is_free(self):
        slot, app = self.prepare_w()
        other = slot / "apps/core"
        shutil.copytree(self.repo, other)
        before = pi_task.w_app_snapshots(slot, app)
        (slot / "logs").mkdir()
        (slot / "logs/test.log").write_text("runtime log")
        (app / "new-test.py").write_text("pass")
        self.assertEqual([], pi_task.w_scope_violations(before))
        (other / "other.txt").write_text("unauthorized")
        self.assertTrue(pi_task.w_scope_violations(before))

    def test_w_mcp_only_exposes_own_site_without_shared_credentials(self):
        slot, app = self.prepare_w()
        (slot / "config").mkdir()
        (slot / "config/mcp.json").write_text(json.dumps({
            "site": "w1.localhost", "url": "http://127.0.0.1:8110",
            "api_key": "own-key", "api_secret": "own-secret"}))
        stage = pi_task.HOME / "stage"
        stage.mkdir()
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(stage)}):
            config, mounts = pi_task.write_mcp(stage, "w1")
        erp = json.loads(config.read_text())["mcpServers"]["erpnext"]
        self.assertEqual("/run/current-system/sw/bin/erpnext-mcp", erp["command"])
        mapping = json.loads(Path(erp["env"]["ERPNEXT_MCP_ENV_MAP"]).read_text())
        self.assertEqual({"w1"}, set(mapping["benches"]))
        creds = json.loads(Path(erp["env"]["ERPNEXT_MCP_CREDENTIALS"]).read_text())
        self.assertEqual({"w1.localhost"}, set(creds))
        self.assertEqual(2, len(mounts))

    def test_worker_and_success_commands_source_same_w_toolchain(self):
        slot, app = self.prepare_w()
        for command in (["pi", "--model", "local-qwen"], ["bash", "-c", "native-tests"]):
            wrapped = pi_task.worker_command(app, command)
            self.assertIn(str(slot), wrapped)
            self.assertEqual(command, wrapped[-len(command):])
            self.assertIn('source "$1/toolchain.sh"', wrapped[2])

    def test_w_mcp_invalid_credentials_are_classified_not_tracebacks(self):
        slot, app = self.prepare_w()
        (slot / "config").mkdir()
        stage = pi_task.HOME / "stage"
        stage.mkdir()
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(stage)}):
            for content in (None, "{", "[]", "{}", '{"site":"w2.localhost"}'):
                if content is not None:
                    (slot / "config/mcp.json").write_text(content)
                with self.assertRaises(pi_task.PolicyError):
                    pi_task.write_mcp(stage, "w1")

    def test_w_app_relink_is_detected_and_broad_scope_is_explicit(self):
        slot, app = self.prepare_w()
        before = pi_task.w_app_links(slot)
        target = slot / "worktrees/replacement"
        target.parent.mkdir()
        shutil.move(str(app), str(target))
        app.symlink_to(target)
        self.assertNotEqual(before, pi_task.w_app_links(slot))
        rules = pi_task.worker_rules("local-qwen", set(), "tests", broad_scope=True)
        self.assertIn("modify and create source files", rules)
        self.assertNotIn("exactly these project-relative files", rules)

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

    def test_model_config_exposes_exactly_tested_production_models(self):
        path = pi_task.HOME / "models.json"
        path.write_text(json.dumps({"providers": {
            "homelab": {"models": [{"id": "strata/qwen3.8-flash"}]},
            "melious": {"models": [{"id": spec["event_model"]}
                for alias, spec in pi_task.MODEL_SPECS.items() if alias != "local-qwen"]},
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
                         "models": [{"id": spec["event_model"]}
                for alias, spec in pi_task.MODEL_SPECS.items() if alias != "local-qwen"]},
        }}))
        stage = pi_task.HOME / "stage-models"
        stage.mkdir()
        rendered = pi_task.write_run_models(stage, source, "melious-dsf", "worker-9")
        config = json.loads(rendered.read_text())
        self.assertEqual("http://127.0.0.1:5098/w/worker-9/v1",
                         config["providers"]["melious"]["baseUrl"])
        self.assertEqual(1, sum(len(value["models"])
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
            frozen = pi_task.freeze_acceptance(stage, [self.repo / "other.txt"])
            protected = pi_task.sandbox_args(self.repo, layout, models, qwen_mcp, qwen_sockets, frozen)
            mount = ["--ro-bind", str(frozen[0][0]), str(self.repo / "other.txt")]
            self.assertTrue(any(protected[i:i+3] == mount for i in range(len(protected)-2)))
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

    def test_length_stop_is_failed_even_when_pi_exits_zero_and_stdin_is_closed(self):
        audit = pi_task.AuditLogger("cutoff-test", "melious-dsf", "cutoff-worker")
        events = pi_task.HOME / "cutoff-events.jsonl"
        script = (
            "import sys,json;assert sys.stdin.read() == '';"
            "print(json.dumps({'type':'message_end','message':{'role':'assistant',"
            "'model':'deepseek-v4.1-flash-1017','stopReason':'length'}}))"
        )
        self.assertEqual(7, pi_task.monitored_run([sys.executable, "-c", script], events, audit, 16))
        records = [json.loads(line) for line in audit.path.read_text().splitlines()]
        cutoff = next(row for row in records if row["kind"] == "length-stop")
        self.assertEqual("cutoff-worker", cutoff["worker_id"])
        self.assertEqual(1, cutoff["turn"])
        self.assertEqual(16, cutoff["max_tokens"])
        self.assertEqual("deepseek-v4.1-flash-1017", cutoff["model"])
        self.assertTrue(pi_task.length_stop({"choices": [{"finish_reason": "length"}]}))
        self.assertFalse(pi_task.length_stop({"type": "message_end", "message": {
            "role": "assistant", "stopReason": "stop"}}))

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
