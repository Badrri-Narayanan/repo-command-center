import unittest
from unittest import mock

from helpers import GitTestCase, git
from rcc.explorer import Explorer, check_rev, split_fields, to_int
from rcc.gitcmd import GitResult
from rcc.operations import Operations


class TestValidation(unittest.TestCase):
    def test_check_rev_accepts_only_hashes_and_head(self):
        for rev in ("HEAD", "abcd", "0123456789abcdef0123456789abcdef01234567"):
            self.assertEqual(check_rev(rev), rev)
        for rev in ("", "--all", "main", "abc", "HEAD~1", "a" * 41, None):
            with self.subTest(rev=rev), self.assertRaises(ValueError):
                check_rev(rev)

    def test_to_int(self):
        self.assertEqual(to_int("3", "n", 0, 5), 3)
        for value in ("x", None, "9", "-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                to_int(value, "n", 0, 5)

    def test_split_fields_pads_stripped_trailing_fields(self):
        self.assertEqual(split_fields("a\x1fb", 4), ["a", "b", "", ""])
        self.assertEqual(split_fields("a\x1fb\x1fc\x1fd", 3), ["a", "b", "c\x1fd"])


class ExplorerCase(GitTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.fx.remote_repo("svc")
        self.repo = self.repository(self.path)
        self.ex = Explorer(self.repo)


class TestLog(ExplorerCase):
    def test_lists_commits_with_refs_and_pages(self):
        for i in range(3):
            self.fx.commit(self.path, f"feat: step {i}", f"f{i}.txt")
        git(self.path, "tag", "v1")
        page = self.ex.log(limit=2)
        self.assertEqual([c["subject"] for c in page["commits"]], ["feat: step 2", "feat: step 1"])
        self.assertTrue(page["more"])
        self.assertIn("tag: v1", page["commits"][0]["refs"])
        self.assertEqual(page["upstream"], "origin/main")
        rest = self.ex.log(skip=2, limit=50)
        self.assertEqual([c["subject"] for c in rest["commits"]], ["feat: step 0", "initial"])
        self.assertFalse(rest["more"])

    def test_incoming_outgoing_and_all(self):
        self.fx.push_upstream("svc", 2)
        git(self.path, "fetch", "-q")
        self.fx.commit(self.path, "local only", "local.txt")
        self.assertEqual([c["subject"] for c in self.ex.log("incoming")["commits"]], ["upstream 1", "upstream 0"])
        self.assertEqual([c["subject"] for c in self.ex.log("outgoing")["commits"]], ["local only"])
        self.assertEqual(len(self.ex.log("all")["commits"]), 4)

    def test_upstream_ranges_without_upstream(self):
        git(self.path, "switch", "-q", "-c", "lonely")
        self.assertEqual(self.ex.log("incoming")["note"], "this branch has no upstream")

    def test_search_messages_and_authors(self):
        self.fx.commit(self.path, "fix: PAY-42 rounding", "a.txt")
        git(self.path, "-c", "user.name=Grace", "-c", "user.email=g@x", "commit", "-q", "--allow-empty",
            "--author=Grace <g@x>", "-m", "chore: bump")
        self.assertEqual([c["subject"] for c in self.ex.log(query="pay-42")["commits"]], ["fix: PAY-42 rounding"])
        self.assertEqual([c["subject"] for c in self.ex.log(query="@grace")["commits"]], ["chore: bump"])
        self.assertEqual(self.ex.log(query="[unbalanced(")["commits"], [])

    def test_author_filter_combines_with_search(self):
        for name, subject in (("Ann", "fix: one"), ("Bob", "fix: two"), ("Bob", "feat: three")):
            git(self.path, "commit", "-q", "--allow-empty", f"--author={name} <{name.lower()}@x.io>", "-m", subject)
        by_bob = self.ex.log(author="bob@x.io")["commits"]
        self.assertEqual([c["subject"] for c in by_bob], ["feat: three", "fix: two"])
        self.assertEqual([c["subject"] for c in self.ex.log(query="fix", author="BOB@x.io")["commits"]], ["fix: two"])
        self.assertEqual(self.ex.log(author="b@x.io")["commits"], [])  # exact email, not a substring

    def test_authors_by_commit_count(self):
        for name in ("Ann", "Bob", "Bob"):
            git(self.path, "commit", "-q", "--allow-empty", f"--author={name} <{name.lower()}@x.io>", "-m", "work")
        git(self.path, "config", "user.email", "ann@x.io")
        data = self.ex.authors()
        self.assertEqual(data["authors"], [{"name": "Bob", "email": "bob@x.io", "count": 2},
                                           {"name": "Ann", "email": "ann@x.io", "count": 1},
                                           {"name": "Test", "email": "test@example.com", "count": 1}])
        self.assertEqual(data["me"], "ann@x.io")
        self.assertEqual(len(self.ex.authors("all")["authors"]), 3)

    def test_authors_edge_cases(self):
        git(self.path, "switch", "-q", "-c", "lonely")
        self.assertEqual(self.ex.authors("incoming")["authors"], [])
        with self.assertRaises(ValueError):
            self.ex.authors("evil")
        replies = [GitResult(1, ""), GitResult(0, "junk line\n  4\tCo <c@x>")]
        with mock.patch.object(self.repo, "run", side_effect=replies):
            self.assertEqual(self.ex.authors(), {"authors": [{"name": "Co", "email": "c@x", "count": 4}], "me": None})

    def test_rejects_bad_input(self):
        for kwargs in ({"range_name": "evil"}, {"skip": "x"}, {"limit": "0"}, {"limit": "999"},
                       {"author": "<x>"}, {"author": "a\nb"}, {"author": "a" * 321}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.ex.log(**kwargs)

    def test_empty_repo_and_broken_log(self):
        empty = self.fx.root / "empty"
        empty.mkdir()
        git(empty, "init", "-q", "-b", "main")
        self.assertEqual(Explorer(self.repository(empty)).log()["note"], "no commits yet")
        failures = [GitResult(128, "", "fatal: boom"), GitResult(0, "abc")]  # log fails, but HEAD exists
        with mock.patch.object(self.repo, "run", side_effect=failures), \
                mock.patch.object(Explorer, "upstream", return_value=None):
            with self.assertRaises(RuntimeError):
                self.ex.log()


class TestCommit(ExplorerCase):
    def test_commit_details_files_and_patch(self):
        (self.path / "README.md").write_text("hello\nworld\n")
        git(self.path, "commit", "-qam", "docs: expand readme\n\nLonger explanation.")
        git(self.path, "mv", "README.md", "DOCS.md")
        git(self.path, "commit", "-qm", "rename")
        head = self.ex.log(limit=2)["commits"]
        detail = self.ex.commit(head[1]["hash"])
        self.assertEqual(detail["subject"], "docs: expand readme")
        self.assertIn("Longer explanation.", detail["body"])
        self.assertEqual(detail["files"], [{"path": "README.md", "status": "M"}])
        self.assertIn("+world", detail["patch"])
        renamed = self.ex.commit(head[0]["short"])
        self.assertEqual(renamed["files"], [{"path": "DOCS.md", "status": "R", "orig": "README.md"}])

    def test_unknown_commit(self):
        with self.assertRaises(LookupError):
            self.ex.commit("deadbeef")

    def test_large_patch_is_truncated(self):
        self.ex.MAX_DIFF_CHARS = 50
        (self.path / "big.txt").write_text("x" * 500)
        git(self.path, "add", "big.txt")
        git(self.path, "commit", "-qm", "big")
        detail = self.ex.commit("HEAD")
        self.assertTrue(detail["truncated"])
        self.assertEqual(len(detail["patch"]), 50)

    def test_failed_patch_raises(self):
        with mock.patch.object(self.repo, "run", side_effect=[GitResult(0, "h\x1fs"), GitResult(0, ""), GitResult(1, "", "bad")]):
            with self.assertRaises(RuntimeError):
                self.ex.commit("HEAD")


class TestChanges(ExplorerCase):
    def make_changes(self):
        (self.path / "README.md").write_text("changed\n")
        (self.path / "staged.txt").write_text("new\n")
        git(self.path, "add", "staged.txt")
        (self.path / "notes").mkdir()
        (self.path / "notes" / " spaced name.txt").write_text("  indented\n")

    def test_lists_staged_unstaged_and_untracked(self):
        self.make_changes()
        files = {f["path"]: f for f in self.ex.changes()["files"]}
        self.assertEqual((files["README.md"]["x"], files["README.md"]["y"]), (" ", "M"))
        self.assertEqual((files["staged.txt"]["x"], files["staged.txt"]["y"]), ("A", " "))
        self.assertEqual(files["notes/ spaced name.txt"]["x"], "?")

    def test_renames_carry_the_original_path(self):
        git(self.path, "mv", "README.md", "DOCS.md")
        self.assertEqual(self.ex.changes()["files"], [{"path": "DOCS.md", "x": "R", "y": " ", "orig": "README.md"}])
        self.assertEqual(self.ex.diff("DOCS.md", staged=True)["files"][0]["status"], "R")

    def test_diffs(self):
        self.make_changes()
        self.assertIn("+changed", self.ex.diff("README.md")["patch"])
        self.assertIn("+new", self.ex.diff("staged.txt", staged=True)["patch"])
        self.assertEqual(self.ex.diff("staged.txt")["patch"], "")
        untracked = self.ex.diff("notes/ spaced name.txt")
        self.assertIn("+  indented", untracked["patch"])
        self.assertEqual(untracked["files"], [{"path": "notes/ spaced name.txt", "status": "A"}])

    def test_binary_untracked_file(self):
        (self.path / "blob.bin").write_bytes(b"\x00\x01\x02")
        self.assertEqual(self.ex.diff("blob.bin")["files"][0]["binary"], True)

    def test_diff_only_for_changed_files(self):
        for path in ("README.md", "../outside", "/etc/passwd"):
            with self.subTest(path=path), self.assertRaises(LookupError):
                self.ex.diff(path)

    def test_status_failure(self):
        with mock.patch.object(self.repo, "run", return_value=GitResult(128, "", "fatal")):
            with self.assertRaises(RuntimeError):
                self.ex.changes()

    def test_porcelain_parser_skips_junk(self):
        self.assertEqual(Explorer._parse_porcelain_z("\0ab\0 M a.txt\0"), [{"path": "a.txt", "x": " ", "y": "M"}])


class TestFile(ExplorerCase):
    def test_reads_file_at_commit_and_in_working_tree(self):
        (self.path / "README.md").write_text("  now\n")
        self.assertEqual(self.ex.file("README.md", "HEAD")["content"], "hello\n")
        self.assertEqual(self.ex.file("README.md")["content"], "  now\n")

    def test_binary_and_truncated(self):
        (self.path / "blob.bin").write_bytes(b"\x00abc")
        git(self.path, "add", "blob.bin")
        git(self.path, "commit", "-qm", "blob")
        self.assertTrue(self.ex.file("blob.bin", "HEAD")["binary"])
        self.assertTrue(self.ex.file("blob.bin")["binary"])
        self.ex.MAX_FILE_BYTES = 3
        self.assertEqual(self.ex.file("README.md"), {"path": "README.md", "rev": "", "binary": False,
                                                     "content": "hel", "truncated": True})

    def test_refuses_bad_paths_and_revs(self):
        for path, rev, error in (("", "", ValueError), ("-x", "", ValueError), ("../x", "", LookupError),
                                 ("missing", "", LookupError), ("missing", "HEAD", LookupError),
                                 ("README.md", "main", ValueError)):
            with self.subTest(path=path, rev=rev), self.assertRaises(error):
                self.ex.file(path, rev)


class TestStashes(ExplorerCase):
    def test_lists_and_shows_stashes_including_untracked(self):
        (self.path / "README.md").write_text("wip\n")
        (self.path / "new.txt").write_text("brand new\n")
        git(self.path, "stash", "push", "-q", "--include-untracked", "-m", "try thing")
        (self.path / "README.md").write_text("second\n")
        git(self.path, "stash", "push", "-q")
        stashes = self.ex.stashes()["stashes"]
        self.assertEqual([(s["index"], s["branch"]) for s in stashes], [(0, "main"), (1, "main")])
        self.assertIn("try thing", stashes[1]["message"])
        shown = self.ex.stash("1")
        self.assertEqual(sorted(f["path"] for f in shown["files"]), ["README.md", "new.txt"])
        self.assertIn("+brand new", shown["patch"])
        self.assertNotIn("new.txt", self.ex.stash(0)["patch"])

    def test_stash_index_validation(self):
        with self.assertRaises(LookupError):
            self.ex.stash(0)
        (self.path / "README.md").write_text("wip\n")
        git(self.path, "stash", "push", "-q")
        for index in ("1", "-1", "x"):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.ex.stash(index)

    def test_parse_stash_without_branch(self):
        self.assertEqual(Explorer._parse_stash("weird\x1fodd subject\x1f5\x1fnow")["branch"], None)

    def test_stash_list_failure(self):
        with mock.patch.object(self.repo, "run", return_value=GitResult(1, "", "bad")):
            with self.assertRaises(RuntimeError):
                self.ex.stashes()

    def test_merge_diffs(self):
        merged = self.ex._merge_diffs({"files": [1], "patch": "a", "truncated": False},
                                      {"files": [2], "patch": "b", "truncated": True})
        self.assertEqual(merged, {"files": [1, 2], "patch": "a\nb", "truncated": True})


class TestStashOperations(ExplorerCase):
    def test_apply_keeps_entry_and_drop_removes_it(self):
        ops = Operations()
        (self.path / "README.md").write_text("wip\n")
        git(self.path, "stash", "push", "-q")
        result = ops.lookup("stash_apply")(self.repo, {"index": 0})
        self.assertEqual((result.result, result.message), ("ok", "applied stash@{0}"))
        self.assertEqual((self.path / "README.md").read_text(), "wip\n")
        self.assertEqual(self.repo.stash_count(), 1)
        result = ops.lookup("stash_drop")(self.repo, {"index": "0"})
        self.assertEqual((result.result, self.repo.stash_count()), ("ok", 0))
        with self.assertRaises(LookupError):
            ops.stash_drop(self.repo, {"index": 0})


class TestTags(ExplorerCase):
    def test_lists_tags_and_latest(self):
        git(self.path, "tag", "v1.0.0")
        self.fx.commit(self.path, "more", "m.txt")
        git(self.path, "tag", "-a", "v1.1.0", "-m", "Release 1.1")
        head = self.ex.log(limit=1)["commits"][0]["hash"]
        data = self.ex.tags()
        self.assertEqual([t["name"] for t in data["tags"]], ["v1.1.0", "v1.0.0"])
        annotated = data["tags"][0]
        self.assertEqual((annotated["annotated"], annotated["commit"], annotated["subject"]), (True, head, "Release 1.1"))
        self.assertFalse(data["tags"][1]["annotated"])
        self.assertEqual(data["latest"], "v1.1.0")

    def test_no_tags(self):
        self.assertEqual(self.ex.tags(), {"tags": [], "latest": None})

    def test_failure(self):
        with mock.patch.object(self.repo, "run", return_value=GitResult(1, "", "bad")):
            with self.assertRaises(RuntimeError):
                self.ex.tags()


class TestHelpers(unittest.TestCase):
    def test_name_status_parsing(self):
        text = "M\ta.txt\nR100\told.txt\tnew.txt\n\nbad\nM\ta.txt"
        self.assertEqual(Explorer._parse_name_status(text),
                         [{"path": "a.txt", "status": "M"}, {"path": "new.txt", "status": "R", "orig": "old.txt"}])

    def test_with_flag_goes_after_subcommand(self):
        self.assertEqual(Explorer._with_flag(("stash", "show", "-p", "s"), "--x"), ("stash", "show", "--x", "-p", "s"))
        self.assertEqual(Explorer._with_flag(("show", "rev"), "--x"), ("show", "--x", "rev"))


if __name__ == "__main__":
    unittest.main()
