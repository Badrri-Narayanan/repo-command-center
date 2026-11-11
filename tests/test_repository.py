import unittest

from helpers import GitTestCase, git
from rcc.repository import Commit, PorcelainParser, RepoStatus, web_url


class TestWebUrl(unittest.TestCase):
    def test_converts_common_remote_formats(self):
        cases = {
            "git@github.com:acme/api.git": "https://github.com/acme/api",
            "git@github-personal:acme/api.git": "https://github-personal/acme/api",
            "ssh://git@gitlab.com/group/sub/api.git": "https://gitlab.com/group/sub/api",
            "https://github.com/acme/api.git": "https://github.com/acme/api",
            "https://user:token@bitbucket.org/acme/api": "https://bitbucket.org/acme/api",
            "http://example.com/acme/api/": "https://example.com/acme/api",
        }
        for remote, expected in cases.items():
            with self.subTest(remote=remote):
                self.assertEqual(web_url(remote), expected)

    def test_unknown_format_returns_none(self):
        self.assertIsNone(web_url("/local/path/repo.git"))


class TestCommit(unittest.TestCase):
    def test_parse(self):
        commit = Commit.parse("abc123\x1ffix: thing\x1fAda\x1f2 days ago\x1f1700000000")
        self.assertEqual(commit, Commit("abc123", "fix: thing", "Ada", "2 days ago", 1700000000))


class TestRepoStatus(unittest.TestCase):
    def test_change_counts(self):
        status = RepoStatus("r", "/r", staged=1, unstaged=2, conflicts=1)
        self.assertEqual(status.tracked_changes, 4)
        self.assertTrue(status.has_changes)
        self.assertTrue(RepoStatus("r", "/r", untracked=1).has_changes)
        self.assertFalse(RepoStatus("r", "/r").has_changes)

    def test_to_dict_uses_api_field_names(self):
        data = RepoStatus("r", "/r", fetched_at=5, commit=Commit("h", "s", "a", "now", 1)).to_dict()
        self.assertEqual(data["fetchedAt"], 5)
        self.assertNotIn("fetched_at", data)
        self.assertEqual(data["commit"]["subject"], "s")


class TestPorcelainParser(unittest.TestCase):
    def parse(self, text):
        return PorcelainParser(RepoStatus("r", "/r")).feed(text)

    def test_branch_headers(self):
        s = self.parse("# branch.oid abc\n# branch.head main\n# branch.upstream origin/main\n# branch.ab +2 -3")
        self.assertEqual((s.branch, s.upstream, s.ahead, s.behind, s.detached), ("main", "origin/main", 2, 3, False))

    def test_detached_head(self):
        s = self.parse("# branch.head (detached)")
        self.assertTrue(s.detached)
        self.assertIsNone(s.branch)

    def test_file_entries(self):
        s = self.parse("\n".join([
            "1 M. N... 100644 100644 100644 a b staged.txt",
            "1 .M N... 100644 100644 100644 a b unstaged.txt",
            "1 MM N... 100644 100644 100644 a b both.txt",
            "2 R. N... 100644 100644 100644 a b R100 new.txt\told.txt",
            "u UU N... 100644 100644 100644 100644 a b c conflict.txt",
            "? untracked.txt",
            "! ignored.txt",
        ]))
        self.assertEqual((s.staged, s.unstaged, s.conflicts, s.untracked), (3, 2, 1, 1))


class TestRepositoryStatus(GitTestCase):
    def test_clean_clone(self):
        path = self.fx.remote_repo("svc")
        git(path, "remote", "set-url", "origin", "git@github.com:acme/svc.git")
        s = self.repository(path).status()
        self.assertIsNone(s.error)
        self.assertEqual((s.name, s.branch, s.default, s.upstream), ("svc", "main", "main", "origin/main"))
        self.assertEqual((s.ahead, s.behind, s.tracked_changes, s.untracked, s.stashes), (0, 0, 0, 0, 0))
        self.assertEqual(s.commit.subject, "initial")
        self.assertEqual(s.web, "https://github.com/acme/svc")

    def test_repr_names_the_repo(self):
        self.assertEqual(repr(self.repository(self.fx.local_repo("svc"))), "Repository('svc')")

    def test_counts_changes_and_stashes(self):
        path = self.fx.remote_repo("svc")
        (path / "README.md").write_text("changed")
        (path / "new.txt").write_text("n")
        git(path, "add", "new.txt")
        (path / "loose.txt").write_text("u")
        s = self.repository(path).status()
        self.assertEqual((s.staged, s.unstaged, s.untracked), (1, 1, 1))
        git(path, "stash", "-q", "-u")
        self.assertEqual(self.repository(path).status().stashes, 1)

    def test_ahead_behind_and_fetch_time(self):
        path = self.fx.remote_repo("svc")
        self.fx.push_upstream("svc", 2)
        git(path, "fetch", "-q")
        self.fx.commit(path, "local")
        s = self.repository(path).status()
        self.assertEqual((s.ahead, s.behind), (1, 2))
        self.assertIsNotNone(s.fetched_at)

    def test_merge_conflicts(self):
        path = self.fx.local_repo("svc")
        self.fx.commit(path, "base", "f.txt", "base\n")
        git(path, "checkout", "-q", "-b", "other")
        self.fx.commit(path, "theirs", "f.txt", "theirs\n")
        git(path, "checkout", "-q", "main")
        self.fx.commit(path, "ours", "f.txt", "ours\n")
        git(path, "merge", "-q", "other", check=False)
        self.assertEqual(self.repository(path).status().conflicts, 1)

    def test_broken_repo_reports_error(self):
        broken = self.fx.root / "broken"
        (broken / ".git").mkdir(parents=True)
        s = self.repository(broken).status()
        self.assertTrue(s.error)
        self.assertIsNone(s.commit)

    def test_repo_without_commits_or_remote(self):
        empty = self.fx.root / "empty"
        empty.mkdir()
        git(empty, "init", "-q", "-b", "main")
        s = self.repository(empty).status()
        self.assertIsNone(s.error)
        self.assertIsNone(s.commit)
        self.assertIsNone(s.web)


class TestRepositoryBranches(GitTestCase):
    def test_default_branch_from_origin_head(self):
        self.assertEqual(self.repository(self.fx.remote_repo("svc", branch="develop")).default_branch(), "develop")

    def test_default_branch_fallbacks(self):
        path = self.fx.remote_repo("svc")
        git(path, "remote", "set-head", "origin", "-d")
        self.assertEqual(self.repository(path).default_branch(), "main")
        self.assertEqual(self.repository(self.fx.local_repo("old", branch="master")).default_branch(), "master")
        self.assertEqual(self.repository(self.fx.local_repo("odd", branch="trunk")).default_branch(), "main")

    def test_branch_name_validation(self):
        repo = self.repository(self.fx.local_repo("svc"))
        self.assertTrue(repo.is_valid_branch_name("feat/x-1"))
        for bad in ("", "-rf", "a..b", "has space", "end."):
            with self.subTest(name=bad):
                self.assertFalse(repo.is_valid_branch_name(bad))

    def test_lists_local_and_remote_without_duplicates(self):
        path = self.fx.remote_repo("svc")
        self.fx.push_branch("svc", "feature/remote-only")
        git(path, "fetch", "-q")
        git(path, "branch", "local-only")
        data = self.repository(path).branches()
        by_name = {b["name"]: b for b in data["branches"]}
        self.assertEqual(data["current"], "main")
        self.assertEqual(sorted(by_name), ["feature/remote-only", "local-only", "main"])
        self.assertTrue(by_name["main"]["local"])
        self.assertFalse(by_name["feature/remote-only"]["local"])

    def test_listing_branches_outside_a_repo_raises(self):
        not_a_repo = self.fx.root / "plain"
        not_a_repo.mkdir()
        with self.assertRaises(RuntimeError):
            self.repository(not_a_repo).branches()


class TestGitDir(GitTestCase):
    def test_regular_and_worktree(self):
        main = self.fx.local_repo("main-repo")
        worktree = self.fx.root / "wt"
        git(main, "worktree", "add", "-q", str(worktree), "-b", "feat")
        self.assertEqual(self.repository(main).git_dir(), main / ".git")
        self.assertTrue((self.repository(worktree).git_dir() / "HEAD").exists())

    def test_unrecognised_dot_git_file(self):
        odd = self.fx.root / "odd"
        odd.mkdir()
        (odd / ".git").write_text("something else")
        self.assertEqual(self.repository(odd).git_dir(), odd / ".git")


if __name__ == "__main__":
    unittest.main()
