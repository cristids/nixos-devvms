import importlib.util
import json
from pathlib import Path
import shutil
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

    def run_external_privacy_probe(self, workspace, head_blob, historical_blob):
        bwrap = shutil.which("bwrap")
        git = str(Path(shutil.which("git")).resolve())
        python = str(Path(sys.executable).resolve())
        script = r'''
import pathlib
import subprocess

assert pathlib.Path("allowed.txt").is_file()
assert not pathlib.Path("secret-head.txt").exists()
assert not pathlib.Path("historical.txt").exists()
assert not pathlib.Path(".git").exists()
assert not any(path.name == ".git" for path in pathlib.Path(".").rglob(".git"))
commands = [
    [GIT, "rev-parse", "HEAD"],
    [GIT, "show", "HEAD:secret-head.txt"],
    [GIT, "cat-file", "-p", HEAD_BLOB],
    [GIT, "cat-file", "-p", HISTORICAL_BLOB],
    [GIT, "log", "-p", "--all"],
    [GIT, "config", "--local", "--list", "--show-origin"],
    [GIT, "remote", "-v"],
    [GIT, "rev-parse", "--git-path", "objects/info/alternates"],
    [GIT, "fsck", "--full"],
]
for command in commands:
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert result.returncode != 0, (command, result.stdout, result.stderr)
pathlib.Path("allowed.txt").write_text("external approved edit\n")
'''.replace("GIT", repr(git)).replace("HEAD_BLOB", repr(head_blob)).replace(
            "HISTORICAL_BLOB", repr(historical_blob))
        result = subprocess.run([
            bwrap, "--die-with-parent", "--unshare-pid", "--unshare-ipc",
            "--unshare-uts", "--new-session", "--clearenv",
            "--ro-bind", "/nix/store", "/nix/store", "--proc", "/proc",
            "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/work",
            "--bind", str(workspace), "/work", "--chdir", "/work",
            "--", python, "-c", script,
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

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

    def test_external_privacy_view_rejects_nested_git_metadata(self):
        nested = self.repo / "vendor/component/.git"
        nested.mkdir(parents=True)
        (nested / "config").write_text("private nested repository metadata\n")
        stage = pi_task.HOME / "nested-git-stage"
        stage.mkdir()
        with self.assertRaisesRegex(pi_task.PolicyError, "Git metadata is forbidden"):
            pi_task.stage_external_workspace(self.repo, stage, {"vendor"})

    def test_external_sandbox_has_no_head_history_config_objects_or_alternates(self):
        (self.repo / "historical.txt").write_text("DELETED-HISTORY-CANARY\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "historical.txt"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "history canary"],
                       check=True)
        historical_blob = subprocess.check_output([
            "git", "-C", str(self.repo), "rev-parse", "HEAD:historical.txt"], text=True).strip()
        subprocess.run(["git", "-C", str(self.repo), "rm", "-q", "historical.txt"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "delete history canary"],
                       check=True)
        (self.repo / "secret-head.txt").write_text("UNAPPROVED-HEAD-CANARY\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "secret-head.txt"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "head canary"],
                       check=True)
        head_blob = subprocess.check_output([
            "git", "-C", str(self.repo), "rev-parse", "HEAD:secret-head.txt"], text=True).strip()
        subprocess.run(["git", "-C", str(self.repo), "remote", "add", "origin",
                        "https://ORIGIN-CONFIG-CANARY.invalid/private.git"], check=True)
        alternate = pi_task.HOME / "scratch/alternate.git"
        subprocess.run(["git", "init", "--bare", "-q", str(alternate)], check=True)
        alternates = self.repo / ".git/objects/info/alternates"
        alternates.write_text(str(alternate / "objects") + "\n")

        linked = pi_task.HOME / "scratch/privacy-linked"
        subprocess.run(["git", "-C", str(self.repo), "worktree", "add", "-qb",
                        "privacy-linked", str(linked)], check=True)
        for label, checkout in (("normal", self.repo), ("linked", linked)):
            with self.subTest(checkout=label):
                layout = pi_task.git_layout(checkout)
                before = pi_task.snapshot(checkout)
                stage = pi_task.HOME / f"privacy-{label}"
                stage.mkdir()
                workspace = pi_task.stage_external_workspace(
                    checkout, stage, {"allowed.txt"})
                workspace_before = pi_task.tree_snapshot(workspace)
                mounts = pi_task.project_mount_args(checkout, layout, workspace)
                mount_text = "\n".join(mounts)
                self.assertIn(str(workspace), mount_text)
                self.assertNotIn(str(layout.dotgit), mount_text)
                self.assertNotIn(str(layout.gitdir), mount_text)
                self.assertNotIn(str(layout.common), mount_text)
                self.run_external_privacy_probe(workspace, head_blob, historical_blob)
                self.assertEqual([], pi_task.tree_violations(
                    workspace_before, pi_task.tree_snapshot(workspace), {"allowed.txt"}))
                self.assertEqual([], pi_task.policy_violations(before, pi_task.snapshot(checkout),
                                                               set()))
                pi_task.apply_external_changes(workspace, checkout, {"allowed.txt"})
                self.assertEqual("external approved edit\n",
                                 (checkout / "allowed.txt").read_text())
                self.assertEqual([], pi_task.policy_violations(
                    before, pi_task.snapshot(checkout), {"allowed.txt"}))
                self.assertEqual("UNAPPROVED-HEAD-CANARY\n",
                                 (checkout / "secret-head.txt").read_text())

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
