"""The folder being managed and the repositories found in it."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from .gitcmd import Git
from .repository import Repository


class Workspace:
    SKIP_DIRS = frozenset({"node_modules", "venv", "build", "dist", "target", "vendor", "__pycache__"})
    MIN_DEPTH, MAX_DEPTH = 1, 4

    def __init__(self, root: Path, depth: int = 1, git: Optional[Git] = None, exclude: Optional[Path] = None):
        self.git = git or Git()
        self.root = Path(root).resolve()
        self.depth = self.check_depth(depth)
        self._exclude = exclude.resolve() if exclude else None
        self._repos: Dict[str, Repository] = {}

    def discover(self) -> Dict[str, Repository]:
        found: Dict[str, Repository] = {}
        self._walk(self.root, 1, found)
        self._repos = found
        return dict(found)

    def _walk(self, folder: Path, level: int, found: Dict[str, Repository]):
        for child in self._candidate_folders(folder):
            if (child / ".git").exists():
                name = child.relative_to(self.root).as_posix()
                found[name] = Repository(child, name, self.git)  # a repo: don't descend into it
            elif level < self.depth:
                self._walk(child, level + 1, found)

    def _candidate_folders(self, folder: Path) -> List[Path]:
        try:
            children = sorted(folder.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return []
        return [c for c in children if self._is_candidate(c)]

    def _is_candidate(self, path: Path) -> bool:
        if path.name.startswith(".") or path.name in self.SKIP_DIRS or not path.is_dir():
            return False
        return path.resolve() != self._exclude

    def resolve(self, name: str) -> Repository:
        if name not in self._repos:
            self.discover()
        try:
            return self._repos[name]
        except KeyError:
            raise LookupError(f"unknown repo: {name}") from None

    def change_root(self, path_text: str, depth: Optional[int] = None) -> dict:
        root = self._resolve_folder(path_text)
        self.depth = self.check_depth(self.depth if depth is None else depth)
        self.root = root
        return {"root": str(self.root), "count": len(self.discover())}

    def _resolve_folder(self, path_text: str) -> Path:
        path = Path(str(path_text).strip() or ".").expanduser()
        path = (path if path.is_absolute() else self.root / path).resolve()
        if not path.is_dir():
            raise ValueError(f"not a folder: {path}")
        return path

    @classmethod
    def check_depth(cls, depth) -> int:
        valid = isinstance(depth, int) and not isinstance(depth, bool) and cls.MIN_DEPTH <= depth <= cls.MAX_DEPTH
        if not valid:
            raise ValueError(f"depth must be {cls.MIN_DEPTH}-{cls.MAX_DEPTH}")
        return depth
