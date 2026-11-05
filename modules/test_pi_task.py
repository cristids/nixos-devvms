import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("pi_task", Path(__file__).with_name("pi-task.py"))
pi_task = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pi_task
SPEC.loader.exec_module(pi_task)


class PiTaskPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_home = pi_task.HOME
        pi_task.HOME = Path(self.temp.name)
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
        pi_task.HOME = self.old_home
        self.temp.cleanup()

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

    def test_worker_id_is_explicitly_scoped_to_dsf(self):
        dsf = pi_task.parse_args([
            "--model", "melious-dsf", "--worker-id", "review-17",
            "--success", "true", "brief",
        ])
        self.assertEqual("review-17", dsf.worker_id)

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

    def test_external_workspace_applies_only_allowed_files(self):
        stage = pi_task.HOME / "stage"
        stage.mkdir()
        workspace = pi_task.stage_external_workspace(
            self.repo, stage, {"allowed.txt", "other.txt"})
        before = pi_task.tree_snapshot(workspace)
        (workspace / "allowed.txt").write_text("external change\n")
        self.assertEqual([], pi_task.tree_violations(
            before, pi_task.tree_snapshot(workspace), {"allowed.txt"}))
        pi_task.apply_external_changes(workspace, self.repo, {"allowed.txt"})
        self.assertEqual("external change\n", (self.repo / "allowed.txt").read_text())
        (workspace / "other.txt").write_text("bad\n")
        self.assertIn("out-of-scope external workspace path changed: other.txt",
                      pi_task.tree_violations(before, pi_task.tree_snapshot(workspace),
                                              {"allowed.txt"}))

    def test_dsf_read_scope_rejects_escape_and_accepts_approved_directory(self):
        src = self.repo / "src"
        src.mkdir()
        (src / "module.py").write_text("pass\n")
        readable = pi_task.validate_read_scope(self.repo, ["src"], {"allowed.txt"})
        self.assertEqual({"allowed.txt", "src"}, readable)
        with self.assertRaises(pi_task.PolicyError):
            pi_task.validate_read_scope(self.repo, ["../secret"], {"allowed.txt"})

    def test_external_privacy_view_rejects_even_in_repo_symlinks(self):
        (self.repo / "private.txt").write_text("not approved\n")
        (self.repo / "approved-link").symlink_to("private.txt")
        stage = pi_task.HOME / "privacy-stage"
        stage.mkdir()
        with self.assertRaisesRegex(pi_task.PolicyError, "symlinks are not allowed"):
            pi_task.stage_external_workspace(self.repo, stage, {"approved-link"})

    def test_accounting_refuses_observed_model_fallback(self):
        events = pi_task.HOME / "events.jsonl"
        events.write_text(json.dumps({
            "type": "message_end",
            "message": {"role": "assistant", "model": "deepseek-v4.1-flash",
                        "usage": {"input": 10, "output": 2, "cost": {"total": 0.1}}},
        }) + "\n")
        with self.assertRaisesRegex(pi_task.PolicyError, "model identity mismatch"):
            pi_task.accounting(events, "melious-dsf", 1.0, 0)


if __name__ == "__main__":
    unittest.main()
