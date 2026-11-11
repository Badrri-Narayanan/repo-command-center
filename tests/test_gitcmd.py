import subprocess
import unittest
from pathlib import Path

from helpers import git  # noqa: F401  (also isolates git config)
from rcc.gitcmd import Git, GitResult


class FakeRunner:
    def __init__(self, returncode=0, stdout="", stderr="", raises=None):
        self.result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        self.raises = raises
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self.raises:
            raise self.raises
        return self.result


class TestGitResult(unittest.TestCase):
    def test_ok_and_combined_output(self):
        result = GitResult(0, "out", "err")
        self.assertTrue(result.ok)
        self.assertEqual(result.output, "out\nerr")

    def test_error_line_prefers_stderr_first_line(self):
        self.assertEqual(GitResult(1, "out", "fatal: boom\nmore").error_line, "fatal: boom")
        self.assertEqual(GitResult(1, "only stdout", "").error_line, "only stdout")
        self.assertEqual(GitResult(128).error_line, "git exited 128")


class TestGit(unittest.TestCase):
    def test_runs_git_in_repo_and_strips_output(self):
        runner = FakeRunner(0, " hello \n", " warn \n")
        result = Git(runner=runner).run(Path("/repo"), "status")
        self.assertEqual(result, GitResult(0, "hello", "warn"))
        cmd, kwargs = runner.calls[0]
        self.assertEqual(cmd, ["git", "-C", str(Path("/repo")), "status"])
        self.assertEqual(kwargs["encoding"], "utf-8")

    def test_never_prompts_for_credentials(self):
        runner = FakeRunner()
        Git(runner=runner).run(Path("."), "fetch")
        env = runner.calls[0][1]["env"]
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertIn("BatchMode=yes", env["GIT_SSH_COMMAND"])

    def test_timeout_becomes_a_result(self):
        runner = FakeRunner(raises=subprocess.TimeoutExpired("git", 5))
        result = Git(runner=runner, timeout=5).run(Path("."), "fetch")
        self.assertEqual(result, GitResult(124, "", "timed out after 5s"))

    def test_succeeds_against_real_git(self):
        self.assertTrue(Git().succeeds(Path("."), "--version"))
        self.assertFalse(Git().succeeds(Path("."), "no-such-command"))


if __name__ == "__main__":
    unittest.main()
