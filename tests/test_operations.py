import unittest
from pathlib import Path
from unittest import mock

from helpers import GitTestCase, git
from rcc.gitcmd import GitResult
from rcc.openers import Opener, OpenerRegistry
from rcc.operations import Operations, OpResult, launch_detached
from rcc.policy import Decision, SwitchPolicy
from rcc.repository import RepoStatus


class TestOpResult(unittest.TestCase):
    def test_from_git(self):
        self.assertEqual(OpResult.from_git(GitResult(0, "out", "err"), "done"), OpResult("ok", "done", "out\nerr"))
        self.assertEqual(OpResult.from_git(GitResult(1, "", "fatal: x\ny"), "done"), OpResult("error", "fatal: x", "fatal: x\ny"))

    def test_constructors_and_dict(self):
        self.assertTrue(OpResult.ok("m").succeeded)
        self.assertFalse(OpResult.skipped("m").succeeded)
        self.assertEqual(OpResult.error("m").to_dict(), {"result": "error", "message": "m", "output": ""})


class TestSwitchPolicy(unittest.TestCase):
    def test_tracked_changes_block_untracked_do_not(self):
        policy = SwitchPolicy()
        self.assertEqual(policy.decide(None, RepoStatus("r", "/r", untracked=3)), Decision(True))
        decision = policy.decide(None, RepoStatus("r", "/r", staged=1, unstaged=2))
        self.assertFalse(decision.proceed)
        self.assertIn("3 uncommitted", decision.reason)


class OperationsTestCase(GitTestCase):
    def setUp(self):
        super().setUp()
        self.launched = []
        opener = Opener("tool", "Tool", "T", "editor", ("tool", "--dir={path}"))
        self.ops = Operations(openers=OpenerRegistry([opener]),
                              launcher=lambda cmd, cwd: self.launched.append((cmd, cwd)),
                              clock=lambda: "2026-01-01 09:00")
        self.path = self.fx.remote_repo("svc")
        self.repo = self.repository(self.path)

    def branch(self):
        return git(self.path, "branch", "--show-current")


class TestLookup(OperationsTestCase):
    def test_names_include_openers(self):
        self.assertIn("sync", self.ops.names())
        self.assertIn("open_tool", self.ops.names())

    def test_default_clock_is_a_readable_timestamp(self):
        self.assertRegex(Operations()._clock(), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_unknown_operation(self):
        for name in ("rm -rf", "open_missing", None):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.ops.lookup(name)

    def test_open_launches_with_substituted_path(self):
        result = self.ops.lookup("open_tool")(self.repo, {})
        self.assertEqual(result.message, "opened in Tool")
        self.assertEqual(self.launched, [(["tool", f"--dir={self.path}"], self.path)])

    def test_launch_detached_uses_popen(self):
        with mock.patch("rcc.operations.subprocess.Popen") as popen:
            launch_detached(["tool"], Path("/somewhere"))
        self.assertEqual(popen.call_args.kwargs["cwd"], str(Path("/somewhere")))


class TestFetchAndPull(OperationsTestCase):
    def test_fetch(self):
        self.fx.push_upstream("svc")
        self.assertTrue(self.ops.fetch(self.repo).succeeded)
        self.assertEqual(self.repo.status().behind, 1)

    def test_pull_fast_forwards(self):
        self.fx.push_upstream("svc", 2)
        result = self.ops.pull(self.repo)
        self.assertEqual((result.result, result.message), ("ok", "pulled main"))
        self.assertEqual(self.repo.status().behind, 0)

    def test_pull_when_already_up_to_date(self):
        self.assertEqual(self.ops.pull(self.repo).message, "already up to date")

    def test_pull_skips_detached_and_untracked_branches(self):
        git(self.path, "checkout", "-q", "-b", "no-upstream")
        self.assertIn("no upstream", self.ops.pull(self.repo).message)
        git(self.path, "checkout", "-q", "--detach")
        self.assertIn("detached", self.ops.pull(self.repo).message)

    def test_pull_refuses_to_merge_diverged_branch(self):
        self.fx.push_upstream("svc")
        self.fx.commit(self.path, "local work", "local.txt")
        self.assertEqual(self.ops.pull(self.repo).result, "error")


class TestSwitching(OperationsTestCase):
    def test_switch_default_from_feature_branch(self):
        git(self.path, "checkout", "-q", "-b", "feat")
        self.assertEqual(self.ops.switch_default(self.repo).message, "switched to main")
        self.assertEqual(self.branch(), "main")

    def test_already_on_target(self):
        self.assertEqual(self.ops.switch_default(self.repo).message, "already on main")

    def test_dirty_repo_is_skipped(self):
        git(self.path, "checkout", "-q", "-b", "feat")
        (self.path / "README.md").write_text("wip")
        self.assertEqual(self.ops.switch_default(self.repo).result, "skipped")
        self.assertEqual(self.branch(), "feat")

    def test_custom_policy_is_used(self):
        class Never(SwitchPolicy):
            def decide(self, repo, status):
                return Decision(False, "frozen")

        git(self.path, "checkout", "-q", "-b", "feat")
        self.assertEqual(Operations(policy=Never()).switch_default(self.repo).message, "frozen")

    def test_git_failure_is_reported(self):
        git(self.path, "checkout", "-q", "-b", "feat")
        real_run = self.repo.run

        def failing_switch(*args):
            return GitResult(1, "", "fatal: nope") if args[0] == "switch" else real_run(*args)

        with mock.patch.object(self.repo, "run", side_effect=failing_switch):
            result = self.ops.switch(self.repo, "main")
        self.assertEqual((result.result, result.message), ("error", "fatal: nope"))

    def test_checkout_validation(self):
        self.assertEqual(self.ops.checkout(self.repo, {"branch": "-rf"}).result, "error")
        self.assertEqual(self.ops.checkout(self.repo, {"branch": "nope"}).result, "skipped")

    def test_checkout_remote_only_branch_creates_tracking_branch(self):
        self.fx.push_branch("svc", "feature/x")
        git(self.path, "fetch", "-q")
        self.assertTrue(self.ops.checkout(self.repo, {"branch": "origin/feature/x"}).succeeded)
        status = self.repo.status()
        self.assertEqual((status.branch, status.upstream), ("feature/x", "origin/feature/x"))


class TestSync(OperationsTestCase):
    def test_switches_and_pulls(self):
        git(self.path, "checkout", "-q", "-b", "feat")
        self.fx.push_upstream("svc", 3)
        self.assertEqual(self.ops.sync(self.repo).message, "pulled main")
        status = self.repo.status()
        self.assertEqual((status.branch, status.behind), ("main", 0))

    def test_stops_at_first_skip(self):
        git(self.path, "checkout", "-q", "-b", "feat")
        (self.path / "README.md").write_text("wip")
        self.assertEqual(self.ops.sync(self.repo).result, "skipped")
        self.assertEqual(self.branch(), "feat")

    def test_stops_when_fetch_fails(self):
        git(self.path, "remote", "set-url", "origin", str(self.fx.base / "missing.git"))
        self.assertEqual(self.ops.sync(self.repo).result, "error")


class TestStash(OperationsTestCase):
    def test_round_trip_including_untracked(self):
        self.assertEqual(self.ops.stash(self.repo).result, "skipped")
        self.assertEqual(self.ops.stash_pop(self.repo).result, "skipped")
        (self.path / "scratch.txt").write_text("keep me")
        self.assertTrue(self.ops.stash(self.repo).succeeded)
        self.assertFalse((self.path / "scratch.txt").exists())
        self.assertIn("rcc 2026-01-01 09:00", git(self.path, "stash", "list"))
        self.assertTrue(self.ops.stash_pop(self.repo).succeeded)
        self.assertTrue((self.path / "scratch.txt").exists())


class TestPrune(OperationsTestCase):
    def test_deletes_only_merged_branches(self):
        git(self.path, "branch", "merged-a")
        git(self.path, "branch", "merged-b")
        git(self.path, "checkout", "-q", "-b", "unmerged")
        self.fx.commit(self.path, "wip", "wip.txt")
        git(self.path, "checkout", "-q", "main")
        self.assertEqual(self.ops.prune_merged(self.repo).message, "pruned 2: merged-a, merged-b")
        self.assertEqual(sorted(git(self.path, "branch", "--format=%(refname:short)").split()), ["main", "unmerged"])

    def test_keeps_current_branch(self):
        git(self.path, "checkout", "-q", "-b", "current-merged")
        self.assertEqual(self.ops.prune_merged(self.repo).message, "no merged branches to prune")

    def test_reports_branches_git_refused(self):
        git(self.path, "branch", "keep-me")
        git(self.path, "branch", "delete-me")
        real_run = self.repo.run

        def refuse_keep_me(*args):
            if args[:3] == ("branch", "-d", "keep-me"):
                return GitResult(1, "", "error: branch 'keep-me' is locked")
            return real_run(*args)

        with mock.patch.object(self.repo, "run", side_effect=refuse_keep_me):
            result = self.ops.prune_merged(self.repo)
        self.assertEqual(result.message, "pruned 1: delete-me")
        self.assertIn("locked", result.output)

    def test_reports_git_error(self):
        with mock.patch.object(self.repo, "default_branch", return_value="does-not-exist"):
            self.assertEqual(self.ops.prune_merged(self.repo).result, "error")


if __name__ == "__main__":
    unittest.main()
