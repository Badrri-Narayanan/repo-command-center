"""What to do when a branch switch meets uncommitted work."""
from __future__ import annotations

from dataclasses import dataclass

from .repository import RepoStatus, Repository


@dataclass(frozen=True)
class Decision:
    proceed: bool
    reason: str = ""


class SwitchPolicy:
    """Consulted before `switch to default`, `checkout <branch>` and `morning sync`.

    Default: skip repos with tracked changes. Untracked files never block a switch in git,
    so they're ignored. To auto-stash instead, subclass and pass it to Operations:

        class AutoStashPolicy(SwitchPolicy):
            def decide(self, repo, status):
                if status.tracked_changes:
                    repo.run("stash", "push", "-m", "rcc auto-stash")
                return Decision(True)
    """

    def decide(self, repo: Repository, status: RepoStatus) -> Decision:
        if status.tracked_changes:
            return Decision(False, f"skipped: {status.tracked_changes} uncommitted change(s)")
        return Decision(True)
