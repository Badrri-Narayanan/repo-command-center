import os
import unittest
from pathlib import Path
from unittest import mock

from helpers import GitTestCase
from rcc.workspace import Workspace


class TestDiscovery(GitTestCase):
    def workspace(self, depth=1, exclude=None):
        return Workspace(self.fx.root, depth, self.git, exclude)

    def test_finds_top_level_repos_and_skips_noise(self):
        for name in ("beta", "alpha", ".hidden", "node_modules"):
            self.fx.local_repo(name)
        (self.fx.root / "not-a-repo").mkdir()
        (self.fx.root / "file.txt").write_text("x")
        self.assertEqual(list(self.workspace().discover()), ["alpha", "beta"])

    def test_depth_finds_nested_repos_but_not_repos_inside_repos(self):
        self.fx.local_repo("orgA/one")
        self.fx.local_repo("orgB/two")
        self.fx.local_repo("orgA/one/vendored")
        self.assertEqual(list(self.workspace(depth=1).discover()), [])
        self.assertEqual(list(self.workspace(depth=3).discover()), ["orgA/one", "orgB/two"])

    def test_excluded_folder_is_skipped(self):
        app = self.fx.local_repo("repo-command-center")
        self.assertEqual(list(self.workspace(exclude=app).discover()), [])

    def test_unreadable_folder_is_ignored(self):
        with mock.patch.object(Path, "iterdir", side_effect=PermissionError):
            self.assertEqual(self.workspace().discover(), {})

    def test_resolve_rediscovers_and_rejects_unknown(self):
        ws = self.workspace()
        ws.discover()
        late = self.fx.local_repo("late")
        self.assertEqual(ws.resolve("late").path, late)
        with self.assertRaises(LookupError):
            ws.resolve("../etc")


class TestChangeRoot(GitTestCase):
    def setUp(self):
        super().setUp()
        self.ws = Workspace(self.fx.root, 1, self.git)

    def test_switches_folder_and_depth(self):
        self.fx.local_repo("org/svc")
        self.assertEqual(self.ws.change_root(str(self.fx.root), 2), {"root": str(self.fx.root), "count": 1})
        self.assertEqual(self.ws.depth, 2)

    def test_keeps_depth_when_not_given(self):
        self.ws.change_root(str(self.fx.root))
        self.assertEqual(self.ws.depth, 1)

    def test_relative_and_home_paths(self):
        (self.fx.root / "sub").mkdir()
        self.assertEqual(self.ws.change_root("sub")["root"], str(self.fx.root / "sub"))
        home = {"HOME": str(self.fx.base), "USERPROFILE": str(self.fx.base)}
        with mock.patch.dict(os.environ, home):
            self.assertEqual(self.ws.change_root("~/repos")["root"], str(self.fx.root))

    def test_rejects_missing_folder_without_changing_state(self):
        with self.assertRaises(ValueError):
            self.ws.change_root(str(self.fx.base / "missing"))
        self.assertEqual(self.ws.root, self.fx.root)

    def test_rejects_invalid_depth(self):
        for depth in (0, 5, "2", True):
            with self.subTest(depth=depth), self.assertRaises(ValueError):
                self.ws.change_root(str(self.fx.root), depth)


if __name__ == "__main__":
    unittest.main()
