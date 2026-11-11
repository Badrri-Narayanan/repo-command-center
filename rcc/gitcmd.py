"""Thin, non-interactive wrapper around the git command line."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitResult:
    code: int
    out: str = ""
    err: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def output(self) -> str:
        return "\n".join(text for text in (self.out, self.err) if text)

    @property
    def error_line(self) -> str:
        return (self.err or self.out or f"git exited {self.code}").splitlines()[0]


class Git:
    """Runs git so that it fails fast instead of waiting for a password or passphrase."""

    TIMEOUT_EXIT_CODE = 124

    def __init__(self, runner=subprocess.run, timeout: int = 180):
        self._runner = runner
        self._timeout = timeout

    def run(self, repo: Path, *args: str) -> GitResult:
        try:
            done = self._runner(["git", "-C", str(repo), *args], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=self._timeout, env=self.env())
        except subprocess.TimeoutExpired:
            return GitResult(self.TIMEOUT_EXIT_CODE, "", f"timed out after {self._timeout}s")
        return GitResult(done.returncode, done.stdout.strip(), done.stderr.strip())

    def succeeds(self, repo: Path, *args: str) -> bool:
        return self.run(repo, *args).ok

    @staticmethod
    def env() -> dict:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
        env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
        return env
