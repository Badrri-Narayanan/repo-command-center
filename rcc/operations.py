"""Actions run on a repository from a tile or a bulk selection."""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .gitcmd import GitResult
from .openers import Opener, OpenerRegistry
from .policy import SwitchPolicy
from .repository import RepoStatus, Repository

OK, SKIPPED, ERROR = "ok", "skipped", "error"
OPEN_PREFIX = "open_"

Action = Callable[[Repository, dict], "OpResult"]


@dataclass
class OpResult:
    result: str
    message: str
    output: str = ""

    @classmethod
    def ok(cls, message: str, output: str = "") -> "OpResult":
        return cls(OK, message, output)

    @classmethod
    def skipped(cls, message: str) -> "OpResult":
        return cls(SKIPPED, message)

    @classmethod
    def error(cls, message: str, output: str = "") -> "OpResult":
        return cls(ERROR, message, output)

    @classmethod
    def from_git(cls, result: GitResult, ok_message: str) -> "OpResult":
        if result.ok:
            return cls.ok(ok_message, result.output)
        return cls.error(result.error_line, result.output)

    @property
    def succeeded(self) -> bool:
        return self.result == OK

    def to_dict(self) -> dict:
        return asdict(self)


def launch_detached(cmd: List[str], cwd: Path):
    subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=os.name != "nt")


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


class Operations:
    def __init__(self, policy: Optional[SwitchPolicy] = None, openers: Optional[OpenerRegistry] = None,
                 launcher: Callable[[List[str], Path], None] = launch_detached,
                 clock: Callable[[], str] = _timestamp):
        self.policy = policy or SwitchPolicy()
        self.openers = openers or OpenerRegistry()
        self._launch = launcher
        self._clock = clock
        self._actions: Dict[str, Action] = {
            "fetch": self.fetch, "pull": self.pull, "switch_default": self.switch_default,
            "checkout": self.checkout, "sync": self.sync, "stash": self.stash,
            "stash_pop": self.stash_pop, "prune_merged": self.prune_merged,
        }

    def names(self) -> List[str]:
        return [*self._actions, *(OPEN_PREFIX + o.id for o in self.openers)]

    def lookup(self, name) -> Action:
        if name in self._actions:
            return self._actions[name]
        opener = self._opener_for(name)
        if opener:
            return lambda repo, _body: self.open(repo, opener)
        raise ValueError(f"unknown op: {name}")

    def _opener_for(self, name) -> Optional[Opener]:
        if isinstance(name, str) and name.startswith(OPEN_PREFIX):
            return self.openers.get(name[len(OPEN_PREFIX):])
        return None

    def fetch(self, repo: Repository, _body: dict = None) -> OpResult:
        return OpResult.from_git(repo.run("fetch", "--all", "--prune"), "fetched")

    def pull(self, repo: Repository, _body: dict = None) -> OpResult:
        status = repo.status()
        blocker = self._pull_blocker(status)
        if blocker:
            return OpResult.skipped(blocker)
        result = repo.run("pull", "--ff-only")
        if result.ok and "Already up to date" in result.out:
            return OpResult.ok("already up to date", result.out)
        return OpResult.from_git(result, f"pulled {status.branch}")

    @staticmethod
    def _pull_blocker(status: RepoStatus) -> Optional[str]:
        if status.detached:
            return "skipped: detached HEAD"
        if not status.upstream:
            return f"skipped: '{status.branch}' has no upstream"
        return None

    def switch_default(self, repo: Repository, _body: dict = None) -> OpResult:
        return self.switch(repo, repo.default_branch())

    def switch(self, repo: Repository, target: str) -> OpResult:
        status = repo.status()
        if status.branch == target:
            return OpResult.ok(f"already on {target}")
        decision = self.policy.decide(repo, status)
        if not decision.proceed:
            return OpResult.skipped(decision.reason)
        return OpResult.from_git(repo.run("switch", target), f"switched to {target}")

    def checkout(self, repo: Repository, body: dict) -> OpResult:
        branch = self._requested_branch(body)
        if not repo.is_valid_branch_name(branch):
            return OpResult.error(f"invalid branch name: {branch!r}")
        if not repo.branch_exists(branch):
            return OpResult.skipped(f"skipped: no branch '{branch}'")
        return self.switch(repo, branch)

    @staticmethod
    def _requested_branch(body: dict) -> str:
        branch = str(body.get("branch", "")).strip()
        return branch[len("origin/"):] if branch.startswith("origin/") else branch

    def sync(self, repo: Repository, body: dict = None) -> OpResult:
        """Morning sync: fetch, switch to the default branch, fast-forward pull. Stops at the first non-ok step."""
        steps: List[OpResult] = []
        for step in (self.fetch, self.switch_default, self.pull):
            steps.append(step(repo, body))
            if not steps[-1].succeeded:
                break
        output = "\n".join(s.output for s in steps if s.output)
        return OpResult(steps[-1].result, steps[-1].message, output)

    def stash(self, repo: Repository, _body: dict = None) -> OpResult:
        if not repo.status().has_changes:
            return OpResult.skipped("nothing to stash")
        message = f"rcc {self._clock()}"
        return OpResult.from_git(repo.run("stash", "push", "--include-untracked", "-m", message), "stashed")

    def stash_pop(self, repo: Repository, _body: dict = None) -> OpResult:
        if not repo.stash_count():
            return OpResult.skipped("no stash entries")
        return OpResult.from_git(repo.run("stash", "pop"), "stash popped")

    def prune_merged(self, repo: Repository, _body: dict = None) -> OpResult:
        default = repo.default_branch()
        merged = repo.run("branch", "--merged", default, "--format=%(refname:short)")
        if not merged.ok:
            return OpResult.error(merged.err)
        keep = {default, repo.current_branch()}
        victims = [b for b in merged.out.splitlines() if b and b not in keep]
        if not victims:
            return OpResult.ok("no merged branches to prune")
        return self._delete_branches(repo, victims)

    @staticmethod
    def _delete_branches(repo: Repository, names: List[str]) -> OpResult:
        results = {name: repo.run("branch", "-d", name) for name in names}  # -d refuses unmerged work
        deleted = [name for name, r in results.items() if r.ok]
        log = "\n".join(r.out or r.err for r in results.values())
        return OpResult.ok(f"pruned {len(deleted)}: {', '.join(deleted)}", log)

    def open(self, repo: Repository, opener: Opener) -> OpResult:
        self._launch(opener.command_for(repo.path), repo.path)
        return OpResult.ok(f"opened in {opener.label}")
