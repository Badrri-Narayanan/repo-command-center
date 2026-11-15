"""Read-only views of one repository: history, working-tree changes, stashes and tags."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .repository import FIELD_SEP, Repository

LOG_FORMAT = FIELD_SEP.join(("%H", "%h", "%s", "%an", "%ae", "%ct", "%cr", "%D", "%P"))
LOG_RANGES = {"head": ("HEAD",), "all": ("--all",), "incoming": ("HEAD..@{u}",), "outgoing": ("@{u}..HEAD",)}
UPSTREAM_RANGES = ("incoming", "outgoing")
TAG_FORMAT = FIELD_SEP.join(("%(refname:short)", "%(objecttype)", "%(objectname)", "%( *objectname)".replace(" ", ""),
                             "%(creatordate:unix)", "%(creatordate:relative)", "%(contents:subject)"))
STASH_FORMAT = FIELD_SEP.join(("%gd", "%gs", "%ct", "%cr"))
DIFF_OPTS = ("--no-color", "--no-ext-diff", "-M")

_HEX_REV = re.compile(r"^[0-9a-fA-F]{4,40}$")
_STASH_REF = re.compile(r"^stash@\{(\d+)\}$")
_SHORTLOG_LINE = re.compile(r"^(\d+)\s+(.*?)\s*<([^>]*)>$")
_NUL_SNIFF_BYTES = 8000


def split_fields(line: str, count: int) -> List[str]:
    """Git output is stripped, and Python counts \\x1f as whitespace, so trailing empty fields can vanish."""
    return (line.split(FIELD_SEP, count - 1) + [""] * count)[:count]


def check_rev(rev: str) -> str:
    """Only commit hashes and HEAD are accepted, so a value can never be read as a git option."""
    if rev == "HEAD" or _HEX_REV.match(rev or ""):
        return rev
    raise ValueError(f"invalid commit: {rev!r}")


def to_int(value, name: str, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    if not low <= number <= high:
        raise ValueError(f"{name} must be {low}-{high}")
    return number


class Explorer:
    """Everything here only reads: nothing changes the repository, the index or the working tree."""

    PAGE_SIZE = 50
    MAX_PAGE_SIZE = 200
    MAX_SKIP = 1_000_000
    MAX_TAGS = 500
    MAX_AUTHORS = 300
    MAX_DIFF_CHARS = 600_000
    MAX_FILE_BYTES = 1_000_000

    def __init__(self, repo: Repository):
        self.repo = repo

    def log(self, range_name: str = "head", query: str = "", skip=0, limit=None, author: str = "") -> dict:
        self._check_range(range_name)
        skip = to_int(skip or 0, "skip", 0, self.MAX_SKIP)
        limit = to_int(limit or self.PAGE_SIZE, "limit", 1, self.MAX_PAGE_SIZE)
        if range_name in UPSTREAM_RANGES and not self.upstream():
            return {"commits": [], "more": False, "upstream": None, "note": "this branch has no upstream"}
        args = ["log", f"--format={LOG_FORMAT}", f"--skip={skip}", f"--max-count={limit + 1}",
                *self._search_args(query), *self._author_args(author), *LOG_RANGES[range_name], "--"]
        result = self.repo.run(*args)
        if not result.ok:
            return self._empty_log_or_raise(result.err)
        commits = [self._parse_log_line(line) for line in result.out.splitlines() if line]
        return {"commits": commits[:limit], "more": len(commits) > limit, "upstream": self.upstream()}

    @staticmethod
    def _search_args(query: str) -> List[str]:
        """Plain text searches commit messages (case-insensitive); `@name` searches author names and emails."""
        query = (query or "").strip()
        if not query:
            return []
        if query.startswith("@") and len(query) > 1:
            return ["-i", "--fixed-strings", f"--author={query[1:]}"]
        return ["-i", "--fixed-strings", f"--grep={query}"]

    @staticmethod
    def _check_range(range_name: str) -> None:
        if range_name not in LOG_RANGES:
            raise ValueError(f"unknown range: {range_name!r}")

    @staticmethod
    def _author_args(email: str) -> List[str]:
        """An exact author email, as listed by authors(). Combined with a search, both must match."""
        email = (email or "").strip()
        if not email:
            return []
        if any(c in email for c in "<>\n") or len(email) > 320:
            raise ValueError("invalid author")
        return ["-i", "--fixed-strings", f"--author=<{email}>"]

    def authors(self, range_name: str = "head") -> dict:
        """Who committed in this range, most commits first, plus your own git email so the UI can offer "Me"."""
        self._check_range(range_name)
        me = self.repo.run("config", "user.email")
        mine = me.out if me.ok else None
        if range_name in UPSTREAM_RANGES and not self.upstream():
            return {"authors": [], "me": mine}
        result = self.repo.run("shortlog", "-sne", *LOG_RANGES[range_name], "--")
        authors = []
        for line in result.out.splitlines() if result.ok else []:
            match = _SHORTLOG_LINE.match(line.strip())
            if match:
                count, name, email = match.groups()
                authors.append({"name": name, "email": email, "count": int(count)})
        return {"authors": authors[: self.MAX_AUTHORS], "me": mine}

    def _empty_log_or_raise(self, err: str) -> dict:
        if not self.repo.run("rev-parse", "--verify", "--quiet", "HEAD").ok:
            return {"commits": [], "more": False, "upstream": None, "note": "no commits yet"}
        raise RuntimeError(err)

    @staticmethod
    def _parse_log_line(line: str) -> dict:
        full, short, subject, author, email, ts, rel, refs, parents = split_fields(line, 9)
        return {"hash": full, "short": short, "subject": subject, "author": author, "email": email,
                "ts": int(ts or 0), "rel": rel, "refs": [r for r in refs.split(", ") if r],
                "merge": len(parents.split()) > 1}

    def upstream(self) -> Optional[str]:
        result = self.repo.run("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        return result.out if result.ok and result.out else None

    def commit(self, rev: str) -> dict:
        rev = check_rev(rev)
        meta = self.repo.run("show", "-s", f"--format={LOG_FORMAT}{FIELD_SEP}%B", rev, "--")
        if not meta.ok:
            raise LookupError(f"unknown commit: {rev}")
        fields = split_fields(meta.out, 10)
        info = {**self._parse_log_line(FIELD_SEP.join(fields[:9])), "body": fields[9].strip()}
        diff_args = ("show", "--format=", "-m", "--first-parent", *DIFF_OPTS, rev, "--")
        return {**info, **self._diff(diff_args)}

    def changes(self) -> dict:
        result = self.repo.run("status", "--porcelain=v1", "-z", "--untracked-files=all", raw=True)
        if not result.ok:
            raise RuntimeError(result.err)
        return {"files": self._parse_porcelain_z(result.out)}

    @staticmethod
    def _parse_porcelain_z(text: str) -> List[dict]:
        entries = text.split("\0")
        files, i = [], 0
        while i < len(entries):
            entry = entries[i]
            i += 1
            if len(entry) < 4:
                continue
            xy, path = entry[:2], entry[3:]
            item = {"path": path, "x": xy[0], "y": xy[1]}
            if xy[0] in "RC":  # renames/copies are followed by the original path
                item["orig"] = entries[i]
                i += 1
            files.append(item)
        return files

    def diff(self, path: str, staged=False) -> dict:
        change = self._find_change(path)
        if change["x"] == "?":
            return self._untracked_as_patch(path)
        cached = ("--cached",) if staged in (True, "1", "true") else ()
        paths = (change["orig"], path) if cached and "orig" in change else (path,)
        return self._diff(("diff", *cached, *DIFF_OPTS, "--", *paths))

    def _find_change(self, path: str) -> dict:
        match = next((c for c in self.changes()["files"] if c["path"] == path), None)
        if match is None:
            raise LookupError(f"not a changed file: {path!r}")
        return match

    def _untracked_as_patch(self, path: str) -> dict:
        content = self._read_worktree(path)
        file = {"path": path, "status": "A"}
        if content is None:
            return {"files": [{**file, "binary": True}], "patch": "", "truncated": False}
        lines = content.splitlines()
        body = "".join(f"+{line}\n" for line in lines)
        patch = f"diff --git a/{path} b/{path}\nnew file\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{body}"
        return {"files": [file], **self._cap(patch)}

    def file(self, path: str, rev: str = "") -> dict:
        """Full read-only contents of a file, from a commit or (rev empty) from the working tree."""
        if not path or path.startswith("-"):
            raise ValueError("a file path is required")
        if rev:
            result = self.repo.run("show", f"{check_rev(rev)}:{path}", raw=True)
            if not result.ok:
                raise LookupError(result.error_line)
            content = None if "\0" in result.out[:_NUL_SNIFF_BYTES] else result.out
        else:
            content = self._read_worktree(path)
        if content is None:
            return {"path": path, "rev": rev, "binary": True, "content": "", "truncated": False}
        capped = content[:self.MAX_FILE_BYTES]
        return {"path": path, "rev": rev, "binary": False, "content": capped, "truncated": len(capped) < len(content)}

    def _read_worktree(self, path: str) -> Optional[str]:
        """None for binary files. Refuses anything that resolves outside the repository."""
        root = self.repo.path.resolve()
        target = (root / path).resolve()
        if root not in target.parents or not target.is_file():
            raise LookupError(f"not a file in this repo: {path!r}")
        with open(target, "rb") as handle:
            data = handle.read(self.MAX_FILE_BYTES + 1)
        if b"\0" in data[:_NUL_SNIFF_BYTES]:
            return None
        # Match text-mode file reads on every OS: normalize Windows/legacy Mac newlines to LF.
        return data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")

    def stashes(self) -> dict:
        result = self.repo.run("stash", "list", f"--format={STASH_FORMAT}")
        if not result.ok:
            raise RuntimeError(result.err)
        return {"stashes": [self._parse_stash(line) for line in result.out.splitlines() if line]}

    @staticmethod
    def _parse_stash(line: str) -> dict:
        ref, subject, ts, rel = split_fields(line, 4)
        match = _STASH_REF.match(ref)
        branch = re.match(r"^(?:WIP on|On) ([^:]+):", subject)
        return {"ref": ref, "index": int(match.group(1)) if match else 0, "message": subject,
                "branch": branch.group(1) if branch else None, "ts": int(ts or 0), "rel": rel}

    def stash(self, index) -> dict:
        ref = self.stash_ref(index)
        diff = self._diff(("stash", "show", "-p", *DIFF_OPTS, ref))
        if self.repo.run("rev-parse", "--verify", "--quiet", f"{ref}^3").ok:  # untracked files were stashed too
            extra = self._diff(("show", "--format=", *DIFF_OPTS, f"{ref}^3", "--"))
            diff = self._merge_diffs(diff, extra)
        return {"ref": ref, **diff}

    def stash_ref(self, index) -> str:
        count = len(self.stashes()["stashes"])
        if not count:
            raise LookupError("no stash entries")
        return f"stash@{{{to_int(index, 'stash index', 0, count - 1)}}}"

    def _merge_diffs(self, first: dict, second: dict) -> dict:
        patch = first["patch"] + ("\n" if first["patch"] and second["patch"] else "") + second["patch"]
        capped = self._cap(patch)
        return {"files": first["files"] + second["files"], "patch": capped["patch"],
                "truncated": first["truncated"] or second["truncated"] or capped["truncated"]}

    def tags(self) -> dict:
        result = self.repo.run("for-each-ref", "--sort=-v:refname", "--sort=-creatordate", f"--count={self.MAX_TAGS}",
                               f"--format={TAG_FORMAT}", "refs/tags")
        if not result.ok:
            raise RuntimeError(result.err)
        tags = [self._parse_tag(line) for line in result.out.splitlines() if line]
        latest = self.repo.run("describe", "--tags", "--abbrev=0")
        return {"tags": tags, "latest": latest.out if latest.ok else None}

    @staticmethod
    def _parse_tag(line: str) -> dict:
        name, kind, obj, peeled, ts, rel, subject = split_fields(line, 7)
        annotated = kind == "tag"
        return {"name": name, "annotated": annotated, "commit": peeled if annotated else obj,
                "ts": int(ts or 0), "rel": rel, "subject": subject}

    def _diff(self, args: Tuple[str, ...]) -> dict:
        """Runs a diff-producing command twice: once for the file list, once for the patch."""
        names = self.repo.run(*self._with_flag(args, "--name-status"))
        patch = self.repo.run(*args)
        if not patch.ok:
            raise RuntimeError(patch.err)
        return {"files": self._parse_name_status(names.out if names.ok else ""), **self._cap(patch.out)}

    @staticmethod
    def _with_flag(args: Tuple[str, ...], flag: str) -> Tuple[str, ...]:
        """Insert `flag` before any revision/path arguments (right after the subcommand words)."""
        head = 2 if args[0] == "stash" else 1
        return (*args[:head], flag, *args[head:])

    @staticmethod
    def _parse_name_status(text: str) -> List[dict]:
        files: Dict[str, dict] = {}
        for line in text.splitlines():
            parts = line.split("\t")
            if len(parts) < 2 or not parts[0]:
                continue
            status, path = parts[0][0], parts[-1]
            item = {"path": path, "status": status}
            if len(parts) == 3:
                item["orig"] = parts[1]
            files.setdefault(path, item)
        return list(files.values())

    def _cap(self, patch: str) -> dict:
        if len(patch) <= self.MAX_DIFF_CHARS:
            return {"patch": patch, "truncated": False}
        return {"patch": patch[:self.MAX_DIFF_CHARS], "truncated": True}
