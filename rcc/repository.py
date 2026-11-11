"""A single git repository and the status snapshot shown on its tile."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional

from .gitcmd import Git, GitResult

REMOTE = "origin"
LOCAL_PREFIX = "refs/heads/"
REMOTE_PREFIX = f"refs/remotes/{REMOTE}/"
FIELD_SEP = "\x1f"
COMMIT_FORMAT = "%h%x1f%s%x1f%an%x1f%cr%x1f%ct"

_SSH_REMOTE = re.compile(r"^(?:ssh://)?git@([^:/]+)[:/](.+?)(?:\.git)?/?$")
_HTTP_REMOTE = re.compile(r"^https?://(?:[^@/]+@)?(.+?)(?:\.git)?/?$")


def web_url(remote: str) -> Optional[str]:
    """Browser URL for a remote such as git@github.com:org/repo.git (credentials are dropped)."""
    ssh = _SSH_REMOTE.match(remote)
    if ssh:
        return f"https://{ssh.group(1)}/{ssh.group(2)}"
    http = _HTTP_REMOTE.match(remote)
    return f"https://{http.group(1)}" if http else None


@dataclass(frozen=True)
class Commit:
    hash: str
    subject: str
    author: str
    rel: str
    ts: int

    @classmethod
    def parse(cls, line: str) -> "Commit":
        short_hash, subject, author, rel, ts = line.split(FIELD_SEP)
        return cls(short_hash, subject, author, rel, int(ts))


@dataclass
class RepoStatus:
    name: str
    path: str
    branch: Optional[str] = None
    detached: bool = False
    default: Optional[str] = None
    upstream: Optional[str] = None
    ahead: int = 0
    behind: int = 0
    staged: int = 0
    unstaged: int = 0
    untracked: int = 0
    conflicts: int = 0
    stashes: int = 0
    commit: Optional[Commit] = None
    web: Optional[str] = None
    fetched_at: Optional[int] = None
    error: Optional[str] = None

    @property
    def tracked_changes(self) -> int:
        return self.staged + self.unstaged + self.conflicts

    @property
    def has_changes(self) -> bool:
        return self.tracked_changes + self.untracked > 0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["fetchedAt"] = data.pop("fetched_at")
        return data


class PorcelainParser:
    """Fills a RepoStatus from `git status --porcelain=v2 --branch` output."""

    def __init__(self, status: RepoStatus):
        self.status = status

    def feed(self, text: str) -> RepoStatus:
        for line in text.splitlines():
            self._parse_line(line)
        return self.status

    def _parse_line(self, line: str):
        if line.startswith("# "):
            self._parse_header(line[2:])
        elif line[:2] in ("1 ", "2 "):
            self._count_change(line[2:4])
        elif line.startswith("u "):
            self.status.conflicts += 1
        elif line.startswith("? "):
            self.status.untracked += 1

    def _parse_header(self, header: str):
        key, _, value = header.partition(" ")
        if key == "branch.head":
            self.status.detached = value == "(detached)"
            self.status.branch = None if self.status.detached else value
        elif key == "branch.upstream":
            self.status.upstream = value
        elif key == "branch.ab":
            ahead, behind = value.split()
            self.status.ahead, self.status.behind = int(ahead[1:]), int(behind[1:])

    def _count_change(self, xy: str):
        self.status.staged += xy[0] != "."
        self.status.unstaged += xy[1] != "."


class Repository:
    FALLBACK_DEFAULTS = ("main", "master")

    def __init__(self, path: Path, name: str, git: Git):
        self.path = path
        self.name = name
        self.git = git

    def __repr__(self) -> str:
        return f"Repository({self.name!r})"

    def run(self, *args: str) -> GitResult:
        return self.git.run(self.path, *args)

    def status(self) -> RepoStatus:
        status = RepoStatus(name=self.name, path=str(self.path))
        result = self.run("status", "--porcelain=v2", "--branch")
        if not result.ok:
            status.error = result.err or f"git status failed ({result.code})"
            return status
        PorcelainParser(status).feed(result.out)
        self._add_details(status)
        return status

    def _add_details(self, status: RepoStatus):
        status.default = self.default_branch()
        status.commit = self.last_commit()
        status.stashes = self.stash_count()
        status.web = self.web_url()
        status.fetched_at = self.fetched_at()

    def default_branch(self) -> str:
        head = self.run("symbolic-ref", "--quiet", "--short", f"refs/remotes/{REMOTE}/HEAD")
        if head.ok and head.out.startswith(f"{REMOTE}/"):
            return head.out[len(REMOTE) + 1:]
        return next((b for b in self.FALLBACK_DEFAULTS if self.branch_exists(b)), "main")

    def current_branch(self) -> str:
        return self.run("branch", "--show-current").out

    def branch_exists(self, name: str) -> bool:
        return any(self.has_ref(ref) for ref in (LOCAL_PREFIX + name, REMOTE_PREFIX + name))

    def has_ref(self, ref: str) -> bool:
        return self.run("show-ref", "--verify", "--quiet", ref).ok

    def is_valid_branch_name(self, name: str) -> bool:
        return bool(name) and not name.startswith("-") and self.run("check-ref-format", "--branch", name).ok

    def last_commit(self) -> Optional[Commit]:
        result = self.run("log", "-1", f"--format={COMMIT_FORMAT}")
        return Commit.parse(result.out) if result.ok and result.out else None

    def stash_count(self) -> int:
        result = self.run("stash", "list")
        return len(result.out.splitlines()) if result.ok else 0

    def web_url(self) -> Optional[str]:
        result = self.run("remote", "get-url", REMOTE)
        return web_url(result.out) if result.ok else None

    def fetched_at(self) -> Optional[int]:
        fetch_head = self.git_dir() / "FETCH_HEAD"
        return int(fetch_head.stat().st_mtime) if fetch_head.exists() else None

    def git_dir(self) -> Path:
        """.git is usually a folder, but is a 'gitdir: <path>' file for worktrees and submodules."""
        dot_git = self.path / ".git"
        if dot_git.is_file():
            pointer = dot_git.read_text().strip()
            if pointer.startswith("gitdir:"):
                return (self.path / pointer[len("gitdir:"):].strip()).resolve()
        return dot_git

    def branches(self) -> dict:
        result = self.run("for-each-ref", "--sort=-committerdate",
                          "--format=%(refname)%1f%(committerdate:relative)", "refs/heads", REMOTE_PREFIX)
        if not result.ok:
            raise RuntimeError(result.err)
        found: Dict[str, dict] = {}
        for line in result.out.splitlines():
            self._add_branch(found, *line.split(FIELD_SEP))
        return {"current": self.current_branch(), "branches": list(found.values())}

    @staticmethod
    def _add_branch(found: Dict[str, dict], ref: str, rel: str):
        local = ref.startswith(LOCAL_PREFIX)
        name = ref[len(LOCAL_PREFIX if local else REMOTE_PREFIX):]
        if local or (name != "HEAD" and name not in found):
            found[name] = {"name": name, "local": local, "rel": rel}
