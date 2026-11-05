import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("pi_task", Path(__file__).with_name("pi-task.py"))
pi_task = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pi_task)


class PiTaskPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "test@example.invalid"], check=True)
        (self.repo / "allowed.txt").write_text("old\n")
        (self.repo / "other.txt").write_text("stable\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "base"], check=True)

    def tearDown(self):
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


if __name__ == "__main__":
    unittest.main()
