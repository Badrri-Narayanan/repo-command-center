"""Tests for server.py - standard library only (unittest + real temporary git repos).

Run:      python3 -m unittest discover -s tests -v
Coverage: uv run --with coverage coverage run -m unittest discover -s tests && coverage report -m
"""
import http.client
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
    # Isolate from the developer's own git config (hooks, signing, default branch...).
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
}
_saved_env = {}


def setUpModule():
    for k, v in GIT_ENV.items():
        _saved_env[k] = os.environ.get(k)
        os.environ[k] = v


def tearDownModule():
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def git(cwd, *args, check=True):
    return subprocess.run(["git", "-C", str(cwd), *args], check=check,
                          capture_output=True, text=True).stdout.strip()


class Fixture:
    """Builds repos inside a temp dir: <base>/repos/<name> cloned from <base>/remotes/<name>.git,
    with a second clone in <base>/upstream/<name> used to push "someone else's" commits."""

    def __init__(self, base: Path):
        self.base = base
        self.root = base / "repos"
        for d in ("repos", "remotes", "upstream"):
            (base / d).mkdir(parents=True, exist_ok=True)

    def repo(self, name: str, branch: str = "main") -> Path:
        bare = self.base / "remotes" / f"{name}.git"
        up = self.base / "upstream" / name
        git(self.base, "init", "-q", "--bare", "-b", branch, str(bare))
        git(self.base, "clone", "-q", str(bare), str(up))
        self.commit(up, "initial", "README.md", "hello\n")
        git(up, "push", "-q", "origin", branch)
        clone = self.root / name
        clone.parent.mkdir(parents=True, exist_ok=True)
        git(self.base, "clone", "-q", str(bare), str(clone))
        return clone

    def local_repo(self, rel: str, branch: str = "main") -> Path:
        path = self.root / rel
        path.mkdir(parents=True)
        git(path, "init", "-q", "-b", branch)
        self.commit(path, "initial")
        return path

    @staticmethod
    def commit(repo: Path, msg: str, file: str = None, content: str = None):
        if file:
            (repo / file).write_text(content if content is not None else msg)
            git(repo, "add", file)
        git(repo, "commit", "-q", "--allow-empty", "-m", msg)

    def push_upstream(self, name: str, n: int = 1, branch: str = "main"):
        up = self.base / "upstream" / name
        git(up, "checkout", "-q", branch)
        for i in range(n):
            self.commit(up, f"upstream {i}", f"up{i}.txt")
        git(up, "push", "-q", "origin", branch)


class FixtureTestCase(unittest.TestCase):
    """Fresh temp repos per test; server.ROOT/DEPTH point at them and are restored afterwards."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(Path(self._tmp.name).resolve())
        self._saved = (server.ROOT, server.DEPTH)
        server.ROOT, server.DEPTH = self.fx.root, 1

    def tearDown(self):
        server.ROOT, server.DEPTH = self._saved
        server._repos.clear()
        self._tmp.cleanup()


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
                self.assertEqual(server.web_url(remote), expected)

    def test_unknown_format_returns_none(self):
        self.assertIsNone(server.web_url("/local/path/repo.git"))


class TestResults(unittest.TestCase):
    def test_from_git_success_combines_output(self):
        r = server.from_git(0, "out", "err", "done")
        self.assertEqual(r, {"result": "ok", "message": "done", "output": "out\nerr"})

    def test_from_git_error_uses_first_line_of_stderr(self):
        r = server.from_git(1, "", "fatal: boom\nmore detail", "done")
        self.assertEqual((r["result"], r["message"]), ("error", "fatal: boom"))

    def test_from_git_error_without_output(self):
        self.assertEqual(server.from_git(128, "", "", "x")["message"], "git exited 128")


class TestBeforeSwitch(unittest.TestCase):
    base = {"staged": 0, "unstaged": 0, "conflicts": 0, "untracked": 0}

    def test_clean_repo_proceeds(self):
        self.assertEqual(server.before_switch(Path("."), dict(self.base)), (True, ""))

    def test_untracked_files_do_not_block(self):
        self.assertTrue(server.before_switch(Path("."), {**self.base, "untracked": 5})[0])

    def test_tracked_changes_block_with_reason(self):
        ok, why = server.before_switch(Path("."), {**self.base, "staged": 1, "unstaged": 2})
        self.assertFalse(ok)
        self.assertIn("3 uncommitted", why)


class TestGitWrapper(unittest.TestCase):
    def test_timeout_is_reported_not_raised(self):
        with mock.patch.object(server.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("git", 1)):
            self.assertEqual(server.git(Path("."), "status", timeout=1), (124, "", "timed out after 1s"))

    def test_never_prompts_for_credentials(self):
        with mock.patch.object(server.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            server.git(Path("."), "fetch")
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertIn("BatchMode=yes", env["GIT_SSH_COMMAND"])


class TestDiscovery(FixtureTestCase):
    def test_finds_top_level_repos_and_skips_noise(self):
        self.fx.local_repo("alpha")
        self.fx.local_repo("beta")
        (self.fx.root / "not-a-repo").mkdir()
        (self.fx.root / "file.txt").write_text("x")
        self.fx.local_repo(".hidden")
        self.fx.local_repo("node_modules")
        self.assertEqual(list(server.discover()), ["alpha", "beta"])

    def test_depth_finds_nested_repos_but_not_repos_inside_repos(self):
        self.fx.local_repo("orgA/one")
        self.fx.local_repo("orgB/two")
        self.fx.local_repo("orgA/one/vendored")  # inside a repo: must not be listed
        self.assertEqual(list(server.discover()), [])
        server.DEPTH = 3
        self.assertEqual(list(server.discover()), ["orgA/one", "orgB/two"])

    def test_skips_the_app_folder_itself(self):
        app = self.fx.local_repo("repo-command-center")
        with mock.patch.object(server, "HERE", app.resolve()):
            self.assertEqual(list(server.discover()), [])

    def test_unreadable_folder_is_ignored(self):
        with mock.patch.object(Path, "iterdir", side_effect=PermissionError):
            self.assertEqual(server.discover(), {})

    def test_resolve_rediscovers_and_rejects_unknown(self):
        self.fx.local_repo("alpha")
        server.discover()
        late = self.fx.local_repo("late")  # created after the last scan
        self.assertEqual(server.resolve("late"), late)
        with self.assertRaises(LookupError):
            server.resolve("../etc")

    def test_repo_id_falls_back_to_folder_name_outside_root(self):
        self.assertEqual(server.repo_id(self.fx.root / "a" / "b"), "a/b")
        self.assertEqual(server.repo_id(Path("/elsewhere/thing")), "thing")

    def test_git_dir_follows_worktree_pointer(self):
        main = self.fx.local_repo("main-repo")
        wt = self.fx.root / "wt"
        git(main, "worktree", "add", "-q", str(wt), "-b", "feat")
        self.assertEqual(server.git_dir(main), main / ".git")
        self.assertTrue((server.git_dir(wt) / "HEAD").exists())
        self.assertNotEqual(server.git_dir(wt), wt / ".git")

    def test_git_dir_ignores_unrecognised_dot_git_file(self):
        odd = self.fx.root / "odd"
        odd.mkdir()
        (odd / ".git").write_text("something else")
        self.assertEqual(server.git_dir(odd), odd / ".git")


class TestStatus(FixtureTestCase):
    def test_clean_clone(self):
        repo = self.fx.repo("svc")
        git(repo, "remote", "set-url", "origin", "git@github.com:acme/svc.git")
        s = server.repo_status(repo)
        self.assertIsNone(s["error"])
        self.assertEqual((s["name"], s["branch"], s["default"], s["upstream"]), ("svc", "main", "main", "origin/main"))
        self.assertEqual([s[k] for k in ("ahead", "behind", "staged", "unstaged", "untracked", "conflicts", "stashes")],
                         [0] * 7)
        self.assertEqual(s["commit"]["subject"], "initial")
        self.assertEqual(s["web"], "https://github.com/acme/svc")
        self.assertFalse(s["detached"])

    def test_counts_changes_and_stashes(self):
        repo = self.fx.repo("svc")
        (repo / "README.md").write_text("changed")           # unstaged
        (repo / "new.txt").write_text("n")
        git(repo, "add", "new.txt")                          # staged
        (repo / "loose.txt").write_text("u")                 # untracked
        s = server.repo_status(repo)
        self.assertEqual((s["staged"], s["unstaged"], s["untracked"]), (1, 1, 1))
        git(repo, "stash", "-q", "-u")
        self.assertEqual(server.repo_status(repo)["stashes"], 1)

    def test_ahead_and_behind(self):
        repo = self.fx.repo("svc")
        self.fx.push_upstream("svc", 2)
        git(repo, "fetch", "-q")
        self.fx.commit(repo, "local")
        s = server.repo_status(repo)
        self.assertEqual((s["ahead"], s["behind"]), (1, 2))
        self.assertIsNotNone(s["fetchedAt"])

    def test_detached_head(self):
        repo = self.fx.repo("svc")
        git(repo, "checkout", "-q", "--detach")
        s = server.repo_status(repo)
        self.assertTrue(s["detached"])
        self.assertIsNone(s["branch"])

    def test_merge_conflicts(self):
        repo = self.fx.local_repo("svc")
        self.fx.commit(repo, "base", "f.txt", "base\n")
        git(repo, "checkout", "-q", "-b", "other")
        self.fx.commit(repo, "theirs", "f.txt", "theirs\n")
        git(repo, "checkout", "-q", "main")
        self.fx.commit(repo, "ours", "f.txt", "ours\n")
        git(repo, "merge", "-q", "other", check=False)
        self.assertEqual(server.repo_status(repo)["conflicts"], 1)

    def test_broken_repo_reports_error(self):
        broken = self.fx.root / "broken"
        (broken / ".git").mkdir(parents=True)
        s = server.repo_status(broken)
        self.assertTrue(s["error"])
        self.assertIsNone(s["commit"])

    def test_repo_without_commits_or_remote(self):
        empty = self.fx.root / "empty"
        empty.mkdir()
        git(empty, "init", "-q", "-b", "main")
        s = server.repo_status(empty)
        self.assertIsNone(s["error"])
        self.assertIsNone(s["commit"])
        self.assertIsNone(s["web"])


class TestDefaultBranch(FixtureTestCase):
    def test_uses_origin_head(self):
        repo = self.fx.repo("svc", branch="develop")
        self.assertEqual(server.default_branch(repo), "develop")

    def test_falls_back_to_main_then_master(self):
        repo = self.fx.repo("svc")
        git(repo, "remote", "set-head", "origin", "-d")
        self.assertEqual(server.default_branch(repo), "main")
        self.assertEqual(server.default_branch(self.fx.local_repo("old", branch="master")), "master")

    def test_defaults_to_main_when_nothing_matches(self):
        self.assertEqual(server.default_branch(self.fx.local_repo("odd", branch="trunk")), "main")


class TestListBranches(FixtureTestCase):
    def test_merges_local_and_remote_without_duplicates(self):
        repo = self.fx.repo("svc")
        up = self.fx.base / "upstream" / "svc"
        git(up, "checkout", "-q", "-b", "feature/remote-only")
        git(up, "push", "-q", "origin", "feature/remote-only")
        git(repo, "fetch", "-q")
        git(repo, "branch", "local-only")
        data = server.list_branches(repo)
        by_name = {b["name"]: b for b in data["branches"]}
        self.assertEqual(data["current"], "main")
        self.assertTrue(by_name["main"]["local"])
        self.assertTrue(by_name["local-only"]["local"])
        self.assertFalse(by_name["feature/remote-only"]["local"])
        self.assertNotIn("HEAD", by_name)
        self.assertEqual(len(data["branches"]), 3)

    def test_error_raises(self):
        with self.assertRaises(RuntimeError):
            server.list_branches(self.fx.base)  # not a repo


class TestPullAndFetch(FixtureTestCase):
    def setUp(self):
        super().setUp()
        self.repo = self.fx.repo("svc")

    def test_fetch(self):
        self.fx.push_upstream("svc", 1)
        r = server.op_fetch(self.repo, {})
        self.assertEqual(r["result"], "ok")
        self.assertEqual(server.repo_status(self.repo)["behind"], 1)

    def test_pull_fast_forwards(self):
        self.fx.push_upstream("svc", 2)
        r = server.op_pull(self.repo, {})
        self.assertEqual((r["result"], r["message"]), ("ok", "pulled main"))
        self.assertEqual(server.repo_status(self.repo)["behind"], 0)

    def test_pull_already_up_to_date(self):
        self.assertEqual(server.op_pull(self.repo, {})["message"], "already up to date")

    def test_pull_skips_detached_and_untracked_branches(self):
        git(self.repo, "checkout", "-q", "-b", "no-upstream")
        self.assertIn("no upstream", server.op_pull(self.repo, {})["message"])
        git(self.repo, "checkout", "-q", "--detach")
        self.assertIn("detached", server.op_pull(self.repo, {})["message"])

    def test_pull_refuses_to_merge_diverged_branch(self):
        self.fx.push_upstream("svc", 1)
        self.fx.commit(self.repo, "local work", "local.txt")
        self.assertEqual(server.op_pull(self.repo, {})["result"], "error")


class TestSwitching(FixtureTestCase):
    def setUp(self):
        super().setUp()
        self.repo = self.fx.repo("svc")

    def test_switch_default_from_feature_branch(self):
        git(self.repo, "checkout", "-q", "-b", "feat")
        r = server.op_switch_default(self.repo, {})
        self.assertEqual((r["result"], r["message"]), ("ok", "switched to main"))

    def test_switch_default_when_already_there(self):
        self.assertEqual(server.op_switch_default(self.repo, {})["message"], "already on main")

    def test_switch_skips_dirty_repo(self):
        git(self.repo, "checkout", "-q", "-b", "feat")
        (self.repo / "README.md").write_text("wip")
        r = server.op_switch_default(self.repo, {})
        self.assertEqual(r["result"], "skipped")
        self.assertEqual(git(self.repo, "branch", "--show-current"), "feat")

    def test_checkout_rejects_invalid_names(self):
        for bad in ("", "-rf", "a..b", "has space", "end."):
            with self.subTest(branch=bad):
                r = server.op_checkout(self.repo, {"branch": bad})
                self.assertEqual(r["result"], "error")

    def test_checkout_missing_branch_is_skipped(self):
        self.assertEqual(server.op_checkout(self.repo, {"branch": "nope"})["result"], "skipped")

    def test_checkout_remote_only_branch_creates_tracking_branch(self):
        up = self.fx.base / "upstream" / "svc"
        git(up, "checkout", "-q", "-b", "feature/x")
        git(up, "push", "-q", "origin", "feature/x")
        git(self.repo, "fetch", "-q")
        r = server.op_checkout(self.repo, {"branch": "origin/feature/x"})
        self.assertEqual(r["result"], "ok")
        s = server.repo_status(self.repo)
        self.assertEqual((s["branch"], s["upstream"]), ("feature/x", "origin/feature/x"))

    def test_switch_error_is_reported(self):
        real_git = server.git

        def failing_switch(path, *args, **kwargs):
            return (1, "", "fatal: nope") if args[0] == "switch" else real_git(path, *args, **kwargs)

        git(self.repo, "checkout", "-q", "-b", "feat")
        with mock.patch.object(server, "git", side_effect=failing_switch):
            r = server._switch(self.repo, "main")
        self.assertEqual((r["result"], r["message"]), ("error", "fatal: nope"))


class TestSync(FixtureTestCase):
    def test_morning_sync_switches_and_pulls(self):
        repo = self.fx.repo("svc")
        git(repo, "checkout", "-q", "-b", "feat")
        self.fx.push_upstream("svc", 3)
        r = server.op_sync(repo, {})
        self.assertEqual((r["result"], r["message"]), ("ok", "pulled main"))
        s = server.repo_status(repo)
        self.assertEqual((s["branch"], s["behind"]), ("main", 0))

    def test_morning_sync_stops_at_first_skip(self):
        repo = self.fx.repo("svc")
        git(repo, "checkout", "-q", "-b", "feat")
        (repo / "README.md").write_text("wip")
        r = server.op_sync(repo, {})
        self.assertEqual(r["result"], "skipped")
        self.assertEqual(git(repo, "branch", "--show-current"), "feat")

    def test_morning_sync_stops_when_fetch_fails(self):
        repo = self.fx.repo("svc")
        git(repo, "remote", "set-url", "origin", str(self.fx.base / "missing.git"))
        self.assertEqual(server.op_sync(repo, {})["result"], "error")


class TestStashAndPrune(FixtureTestCase):
    def setUp(self):
        super().setUp()
        self.repo = self.fx.repo("svc")

    def test_stash_round_trip_including_untracked(self):
        self.assertEqual(server.op_stash(self.repo, {})["result"], "skipped")
        self.assertEqual(server.op_stash_pop(self.repo, {})["result"], "skipped")
        (self.repo / "scratch.txt").write_text("keep me")
        self.assertEqual(server.op_stash(self.repo, {})["result"], "ok")
        self.assertFalse((self.repo / "scratch.txt").exists())
        self.assertIn("rcc ", git(self.repo, "stash", "list"))
        self.assertEqual(server.op_stash_pop(self.repo, {})["result"], "ok")
        self.assertTrue((self.repo / "scratch.txt").exists())

    def test_prune_deletes_only_merged_branches(self):
        git(self.repo, "branch", "merged-a")
        git(self.repo, "branch", "merged-b")
        git(self.repo, "checkout", "-q", "-b", "unmerged")
        self.fx.commit(self.repo, "wip", "wip.txt")
        git(self.repo, "checkout", "-q", "main")
        r = server.op_prune_merged(self.repo, {})
        self.assertEqual(r["message"], "pruned 2: merged-a, merged-b")
        self.assertEqual(sorted(git(self.repo, "branch", "--format=%(refname:short)").split()), ["main", "unmerged"])

    def test_prune_keeps_current_branch_and_reports_nothing_to_do(self):
        git(self.repo, "checkout", "-q", "-b", "current-merged")
        self.assertEqual(server.op_prune_merged(self.repo, {})["message"], "no merged branches to prune")

    def test_prune_reports_branches_git_refused_to_delete(self):
        git(self.repo, "branch", "keep-me")
        git(self.repo, "branch", "delete-me")
        real_git = server.git

        def refuse_keep_me(path, *args, **kwargs):
            if args[:2] == ("branch", "-d") and args[2] == "keep-me":
                return 1, "", "error: branch 'keep-me' is locked"
            return real_git(path, *args, **kwargs)

        with mock.patch.object(server, "git", side_effect=refuse_keep_me):
            r = server.op_prune_merged(self.repo, {})
        self.assertEqual(r["message"], "pruned 1: delete-me")
        self.assertIn("locked", r["output"])

    def test_prune_reports_git_error(self):
        with mock.patch.object(server, "default_branch", return_value="does-not-exist"):
            self.assertEqual(server.op_prune_merged(self.repo, {})["result"], "error")


class TestOpeners(FixtureTestCase):
    def test_opener_substitutes_path_and_runs_detached(self):
        repo = self.fx.local_repo("svc")
        fake = {"t": {"id": "t", "label": "Tool", "short": "T", "kind": "editor", "cmd": ["tool", "--dir={path}"]}}
        with mock.patch.dict(server.OPENERS, fake, clear=True), \
                mock.patch.object(server.subprocess, "Popen") as popen:
            r = server.make_opener("t")(repo, {})
        self.assertEqual(r["message"], "opened in Tool")
        args, kwargs = popen.call_args
        self.assertEqual(args[0], ["tool", f"--dir={repo}"])
        self.assertEqual(kwargs["cwd"], str(repo))

    def test_every_detected_opener_is_registered_as_an_op(self):
        for oid in server.OPENERS:
            self.assertIn(f"open_{oid}", server.OPS)


class TestToolDetection(unittest.TestCase):
    def which(self, *available):
        return mock.patch.object(server.shutil, "which", side_effect=lambda n: f"/bin/{n}" if n in available else None)

    def test_editor_prefers_cli_then_mac_app(self):
        with self.which("code"):
            self.assertEqual(server._editor("code", "VS"), ["/bin/code", "{path}"])
        with self.which(), mock.patch.object(server, "IS_MAC", True), \
                mock.patch.object(server, "_mac_app", return_value="Visual Studio Code"):
            self.assertEqual(server._editor("code", "Visual Studio Code"),
                             ["open", "-a", "Visual Studio Code", "{path}"])
        with self.which(), mock.patch.object(server, "IS_MAC", False):
            self.assertIsNone(server._editor("code", "Visual Studio Code"))

    def test_mac_app_lookup_in_user_applications(self):
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / "Applications" / "Fancy.app").mkdir(parents=True)
            with mock.patch.object(Path, "home", return_value=Path(home)):
                self.assertEqual(server._mac_app("Nope-xyz", "Fancy"), "Fancy")
                self.assertIsNone(server._mac_app("Nope-xyz"))

    def test_file_manager_per_platform(self):
        with mock.patch.object(server, "IS_MAC", True):
            self.assertEqual(server._file_manager(), ["open", "{path}"])
        with mock.patch.object(server, "IS_MAC", False), mock.patch.object(server, "IS_WIN", True):
            self.assertEqual(server._file_manager(), ["explorer", "{path}"])
        with mock.patch.object(server, "IS_MAC", False), mock.patch.object(server, "IS_WIN", False):
            with self.which("xdg-open"):
                self.assertEqual(server._file_manager(), ["xdg-open", "{path}"])
            with self.which():
                self.assertIsNone(server._file_manager())

    def test_terminal_per_platform(self):
        with mock.patch.object(server, "IS_MAC", True), mock.patch.object(server, "_mac_app", return_value=None):
            self.assertEqual(server._terminal(), ["open", "-a", "Terminal", "{path}"])
        with mock.patch.object(server, "IS_MAC", False), mock.patch.object(server, "IS_WIN", True):
            with self.which("wt"):
                self.assertEqual(server._terminal(), ["wt", "-d", "{path}"])
            with self.which():
                self.assertEqual(server._terminal()[:2], ["cmd", "/c"])
        with mock.patch.object(server, "IS_MAC", False), mock.patch.object(server, "IS_WIN", False):
            with self.which("konsole"):
                self.assertEqual(server._terminal(), ["konsole", "--workdir", "{path}"])
            with self.which():
                self.assertIsNone(server._terminal())


class TestOpenerConfig(unittest.TestCase):
    def build(self, config_text=None):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "cfg.json"
            if config_text is not None:
                cfg.write_text(config_text)
            out = io.StringIO()
            with mock.patch.object(server, "CONFIG_FILE", cfg), \
                    mock.patch.object(server, "_editor", return_value=["ed", "{path}"]), \
                    mock.patch.object(server, "_file_manager", return_value=["fm", "{path}"]), \
                    mock.patch.object(server, "_terminal", return_value=["term", "{path}"]), \
                    redirect_stdout(out):
                return server.build_openers(), out.getvalue()

    def test_builtins_without_config(self):
        openers, _ = self.build()
        self.assertEqual(list(openers), ["vscode", "cursor", "intellij", "files", "terminal"])

    def test_config_adds_overrides_and_hides(self):
        openers, _ = self.build(json.dumps({"openers": [
            {"id": "sublime", "cmd": ["subl", "{path}"]},
            {"id": "terminal", "label": "My Term"},
            {"id": "cursor", "cmd": None},
        ]}))
        self.assertNotIn("cursor", openers)
        self.assertEqual(openers["terminal"]["label"], "My Term")
        self.assertEqual(openers["terminal"]["cmd"], ["term", "{path}"])
        self.assertEqual((openers["sublime"]["short"], openers["sublime"]["kind"]), ("SU", "editor"))

    def test_invalid_config_warns_and_keeps_builtins(self):
        openers, out = self.build("{not json")
        self.assertIn("warning: ignoring", out)
        self.assertIn("vscode", openers)


class TestSetRoot(FixtureTestCase):
    def test_switches_folder_and_depth(self):
        self.fx.local_repo("org/svc")
        r = server.set_root({"path": str(self.fx.root), "depth": 2})
        self.assertEqual(r, {"root": str(self.fx.root), "count": 1})
        self.assertEqual(server.overview()["repos"], ["org/svc"])

    def test_relative_and_home_paths(self):
        (self.fx.root / "sub").mkdir()
        self.assertEqual(server.set_root({"path": "sub"})["root"], str(self.fx.root / "sub"))
        with mock.patch.dict(os.environ, {"HOME": str(self.fx.base)}):
            self.assertEqual(server.set_root({"path": "~/repos"})["root"], str(self.fx.root))

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            server.set_root({"path": str(self.fx.base / "missing")})
        for depth in (0, 5, "2"):
            with self.subTest(depth=depth), self.assertRaises(ValueError):
                server.set_root({"path": str(self.fx.root), "depth": depth})

    def test_overview_shape(self):
        o = server.overview()
        self.assertEqual(set(o), {"root", "home", "depth", "repos", "openers"})
        for opener in o["openers"]:
            self.assertNotIn("cmd", opener)  # commands are never sent to the browser


class TestHttpApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fx = Fixture(Path(cls._tmp.name).resolve())
        cls.fx.repo("svc")
        cls._saved = (server.ROOT, server.DEPTH)
        server.ROOT, server.DEPTH = cls.fx.root, 1
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        server.ROOT, server.DEPTH = cls._saved
        server._repos.clear()
        cls._tmp.cleanup()

    def setUp(self):
        server.ROOT, server.DEPTH = self.fx.root, 1

    def call(self, method, path, body=None, token=True, host="127.0.0.1"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Host": f"{host}:{self.port}", "Content-Type": "application/json"}
        if token:
            headers["X-RCC-Token"] = server.TOKEN if token is True else token
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        ctype = resp.getheader("Content-Type")
        return resp.status, (json.loads(raw) if ctype == "application/json" else raw.decode())

    def test_index_page_embeds_token(self):
        status, html = self.call("GET", "/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(server.TOKEN, html)
        self.assertNotIn("__RCC_TOKEN__", html)

    def test_page_links_its_static_assets(self):
        _, html = self.call("GET", "/", token=False)
        self.assertIn(f'<meta name="rcc-token" content="{server.TOKEN}">', html)
        for path in server.STATIC_FILES:
            self.assertIn(path.lstrip("/"), html)

    def test_static_assets_served_with_content_type(self):
        for path, ctype in server.STATIC_FILES.items():
            with self.subTest(path=path):
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
                conn.request("GET", path, headers={"Host": f"127.0.0.1:{self.port}"})
                resp = conn.getresponse()
                body = resp.read()
                conn.close()
                self.assertEqual((resp.status, resp.getheader("Content-Type")), (200, ctype))
                self.assertGreater(len(body), 1000)

    def test_static_route_cannot_reach_other_files(self):
        for path in ("/static/../server.py", "/static/app.py", "/server.py", "/static/%2e%2e/server.py"):
            with self.subTest(path=path):
                self.assertEqual(self.call("GET", path, token=False)[0], 401)

    def test_rejects_foreign_host_header(self):
        self.assertEqual(self.call("GET", "/", host="evil.example")[0], 403)
        self.assertEqual(self.call("POST", "/api/op", {"op": "fetch", "repo": "svc"}, host="evil.example")[0], 401)

    def test_requires_valid_token(self):
        self.assertEqual(self.call("GET", "/api/repos", token=False)[0], 401)
        self.assertEqual(self.call("GET", "/api/repos", token="wrong")[0], 401)
        self.assertEqual(self.call("POST", "/api/op", {"op": "fetch", "repo": "svc"}, token=False)[0], 401)

    def test_read_endpoints(self):
        status, data = self.call("GET", "/api/repos")
        self.assertEqual((status, data["repos"]), (200, ["svc"]))
        status, data = self.call("GET", "/api/status?repo=svc")
        self.assertEqual((status, data["branch"]), (200, "main"))
        status, data = self.call("GET", "/api/branches?repo=svc")
        self.assertEqual((status, data["current"]), (200, "main"))

    def test_unknown_routes_and_repos(self):
        self.assertEqual(self.call("GET", "/api/nope")[0], 404)
        self.assertEqual(self.call("GET", "/api/status?repo=../../etc")[0], 404)
        self.assertEqual(self.call("POST", "/api/nope", {})[0], 404)

    def test_run_operation_returns_fresh_status(self):
        status, data = self.call("POST", "/api/op", {"op": "fetch", "repo": "svc"})
        self.assertEqual((status, data["result"]), (200, "ok"))
        self.assertEqual(data["status"]["name"], "svc")

    def test_unknown_operation(self):
        status, data = self.call("POST", "/api/op", {"op": "rm -rf", "repo": "svc"})
        self.assertEqual(status, 400)
        self.assertIn("unknown op", data["error"])

    def test_change_root_validation(self):
        self.assertEqual(self.call("POST", "/api/root", {"path": "/definitely/not/here"})[0], 400)
        status, data = self.call("POST", "/api/root", {"path": str(self.fx.root), "depth": 1})
        self.assertEqual((status, data["count"]), (200, 1))

    def test_unexpected_errors_become_500(self):
        with mock.patch.object(server, "repo_status", side_effect=RuntimeError("kaboom")):
            status, data = self.call("GET", "/api/status?repo=svc")
        self.assertEqual(status, 500)
        self.assertEqual(data["error"], "RuntimeError: kaboom")


class TestMain(unittest.TestCase):
    def setUp(self):
        self._saved = (server.ROOT, server.DEPTH)
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name).resolve()

    def tearDown(self):
        server.ROOT, server.DEPTH = self._saved
        self._tmp.cleanup()

    def run_main(self, argv, servers):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["server.py", *argv]), \
                mock.patch.object(server, "ThreadingHTTPServer", side_effect=servers) as srv, \
                mock.patch.object(server.threading, "Timer") as timer, \
                redirect_stdout(out):
            server.main()
        return out.getvalue(), srv, timer

    def fake_server(self):
        fake = mock.Mock()
        fake.serve_forever.side_effect = KeyboardInterrupt
        return fake

    def test_starts_on_next_free_port_and_opens_browser(self):
        out, srv, timer = self.run_main([str(self.folder), "--depth", "2"], [OSError, self.fake_server()])
        self.assertIn("http://127.0.0.1:8766/", out)
        self.assertEqual((server.ROOT, server.DEPTH), (self.folder, 2))
        self.assertEqual(srv.call_count, 2)
        timer.assert_called_once()

    def test_no_browser_and_root_flag(self):
        out, _, timer = self.run_main(["--root", str(self.folder), "--no-browser", "--port", "9100"],
                                      [self.fake_server()])
        self.assertIn(":9100/", out)
        timer.assert_not_called()

    def test_exits_cleanly_on_bad_input(self):
        with self.assertRaises(SystemExit):
            self.run_main([str(self.folder / "missing")], [])
        with self.assertRaises(SystemExit):
            self.run_main([str(self.folder)], [OSError] * 20)
        with mock.patch.object(server.shutil, "which", return_value=None), self.assertRaises(SystemExit):
            self.run_main([str(self.folder)], [])


if __name__ == "__main__":
    unittest.main()
