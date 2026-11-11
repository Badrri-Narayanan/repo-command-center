"""Shared test fixtures: real throwaway git repositories, isolated from the developer's git config."""
import atexit
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rcc.gitcmd import Git  # noqa: E402
from rcc.repository import Repository  # noqa: E402


def _isolate_git_config():
    """Ignore the developer's global/system git config (hooks, signing, autocrlf, default branch)."""
    handle, empty_config = tempfile.mkstemp(suffix=".gitconfig")
    os.close(handle)
    atexit.register(os.remove, empty_config)
    os.environ.update({
        "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_CONFIG_GLOBAL": empty_config, "GIT_CONFIG_NOSYSTEM": "1",
    })


_isolate_git_config()


def git(cwd, *args, check=True) -> str:
    done = subprocess.run(["git", "-C", str(cwd), *args], check=check, capture_output=True, text=True)
    return done.stdout.strip()


class Fixture:
    """Repos live in <base>/repos/<name>, cloned from <base>/remotes/<name>.git.
    <base>/upstream/<name> is a second clone used to push "someone else's" commits."""

    def __init__(self, base: Path):
        self.base = base
        self.root = base / "repos"
        for folder in ("repos", "remotes", "upstream"):
            (base / folder).mkdir(parents=True, exist_ok=True)

    def remote_repo(self, name: str, branch: str = "main") -> Path:
        bare = self.base / "remotes" / f"{name}.git"
        git(self.base, "init", "-q", "--bare", "-b", branch, str(bare))
        upstream = self.upstream(name)
        git(self.base, "clone", "-q", str(bare), str(upstream))
        self.commit(upstream, "initial", "README.md", "hello\n")
        git(upstream, "push", "-q", "origin", branch)
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

    def upstream(self, name: str) -> Path:
        return self.base / "upstream" / name

    @staticmethod
    def commit(repo: Path, message: str, file: str = None, content: str = None):
        if file:
            (repo / file).write_text(message if content is None else content)
            git(repo, "add", file)
        git(repo, "commit", "-q", "--allow-empty", "-m", message)

    def push_upstream(self, name: str, count: int = 1, branch: str = "main"):
        upstream = self.upstream(name)
        git(upstream, "checkout", "-q", branch)
        for i in range(count):
            self.commit(upstream, f"upstream {i}", f"up{i}.txt")
        git(upstream, "push", "-q", "origin", branch)

    def push_branch(self, name: str, branch: str):
        upstream = self.upstream(name)
        git(upstream, "checkout", "-q", "-b", branch)
        git(upstream, "push", "-q", "origin", branch)


class GitTestCase(unittest.TestCase):
    """A fresh temporary folder of repos per test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(Path(self._tmp.name).resolve())
        self.git = Git()

    def tearDown(self):
        self._tmp.cleanup()

    def repository(self, path: Path) -> Repository:
        return Repository(path, path.relative_to(self.fx.root).as_posix(), self.git)
