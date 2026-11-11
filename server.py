#!/usr/bin/env python3
"""Repo Command Center - a local web UI for managing many git repos at once.

Usage (works on macOS, Linux and Windows; needs Python 3.8+ and git):
    python3 /path/to/server.py [FOLDER] [--depth N] [--port 8765] [--no-browser]

FOLDER defaults to the current directory. Only the Python standard library is used. The server binds to 127.0.0.1 and every
API call must carry a per-run random token, so other websites cannot drive it.
"""
import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(24)
ROOT = Path.cwd()


def before_switch(path: Path, status: dict) -> "tuple[bool, str]":
    """Called before `switch to default` / `checkout <branch>` / `morning sync`.

    `status` is the dict produced by repo_status() (staged, unstaged, conflicts,
    untracked, stashes, branch, ...). Return (proceed, message):
      - (True, "")            -> go ahead and switch
      - (False, "reason")     -> skip this repo; the reason is shown on its tile

    Default policy: skip repos with tracked changes (untracked files never block a
    switch in git, so they're ignored). An alternative is auto-stashing:
        git(path, "stash", "push", "-m", "rcc auto-stash") and return (True, "")
    """
    changes = status["staged"] + status["unstaged"] + status["conflicts"]
    if changes:
        return False, f"skipped: {changes} uncommitted change(s)"
    return True, ""


# "Open in" tools - detected per machine; tools that aren't installed are hidden.
# Add or override tools in ~/.repo-command-center.json, e.g.
#   {"openers": [{"id": "sublime", "label": "Sublime Text", "short": "ST",
#                 "kind": "editor", "cmd": ["subl", "{path}"]},
#                {"id": "cursor", "cmd": null}]}          <- null hides a built-in
# "kind": "editor" shows as a tile icon; "system" appears only in the ... menu.
CONFIG_FILE = Path.home() / ".repo-command-center.json"
IS_MAC, IS_WIN = sys.platform == "darwin", os.name == "nt"


def _mac_app(*names):
    for n in names:
        for d in (Path("/Applications"), Path.home() / "Applications"):
            if (d / f"{n}.app").exists():
                return n
    return None


def _editor(cli, *mac_apps):
    """Prefer the CLI shim on PATH; on macOS fall back to the .app bundle."""
    exe = shutil.which(cli)
    if exe:
        return [exe, "{path}"]
    app = _mac_app(*mac_apps) if IS_MAC else None
    return ["open", "-a", app, "{path}"] if app else None


def _file_manager():
    if IS_MAC:
        return ["open", "{path}"]
    if IS_WIN:
        return ["explorer", "{path}"]
    return ["xdg-open", "{path}"] if shutil.which("xdg-open") else None


def _terminal():
    if IS_MAC:
        return ["open", "-a", _mac_app("iTerm") or "Terminal", "{path}"]
    if IS_WIN:
        if shutil.which("wt"):
            return ["wt", "-d", "{path}"]
        return ["cmd", "/c", "start", "cmd", "/K", "cd", "/d", "{path}"]
    for cmd in (["gnome-terminal", "--working-directory={path}"], ["konsole", "--workdir", "{path}"],
                ["xfce4-terminal", "--working-directory={path}"], ["x-terminal-emulator"]):
        if shutil.which(cmd[0]):
            return cmd
    return None


def build_openers() -> dict:
    builtin = [
        ("vscode", "VS Code", "VS", "editor", _editor("code", "Visual Studio Code")),
        ("cursor", "Cursor", "Cu", "editor", _editor("cursor", "Cursor")),
        ("intellij", "IntelliJ IDEA", "IJ", "editor",
         _editor("idea", "IntelliJ IDEA", "IntelliJ IDEA Ultimate", "IntelliJ IDEA CE")),
        ("files", "Finder" if IS_MAC else "File manager", "📁", "system", _file_manager()),
        ("terminal", "Terminal", ">_", "system", _terminal()),
    ]
    openers = {i: {"id": i, "label": lbl, "short": s, "kind": k, "cmd": c} for i, lbl, s, k, c in builtin}
    try:
        for o in json.loads(CONFIG_FILE.read_text()).get("openers", []):
            base = openers.get(o["id"], {"label": o["id"], "short": o["id"][:2].upper(), "kind": "editor"})
            openers[o["id"]] = {**base, **o}
    except FileNotFoundError:
        pass
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        print(f"warning: ignoring {CONFIG_FILE}: {e}")
    return {k: v for k, v in openers.items() if v.get("cmd")}


OPENERS = build_openers()


def git(repo: Path, *args: str, timeout: int = 180):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")  # never hang on a prompt
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                           text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    return p.returncode, p.stdout.strip(), p.stderr.strip()


DEPTH = 1  # how many folder levels below ROOT to look for repos
SKIP_DIRS = {"node_modules", "venv", "build", "dist", "target", "vendor", "__pycache__"}
_repos: dict = {}  # repo id (path relative to ROOT) -> Path; refreshed by discover()


def discover() -> dict:
    found = {}

    def walk(d: Path, level: int):
        try:
            children = sorted(d.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for p in children:
            if p.name.startswith(".") or p.name in SKIP_DIRS or not p.is_dir() or p.resolve() == HERE:
                continue
            if (p / ".git").exists():
                found[p.relative_to(ROOT).as_posix()] = p  # a repo: don't descend into it
            elif level < DEPTH:
                walk(p, level + 1)

    walk(ROOT, 1)
    _repos.clear()
    _repos.update(found)
    return found


def resolve(name: str) -> Path:
    if name not in _repos:
        discover()
    if name in _repos:
        return _repos[name]
    raise LookupError(f"unknown repo: {name}")


def repo_id(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:  # root changed while a request was in flight
        return path.name


def git_dir(path: Path) -> Path:
    """.git is usually a folder, but is a 'gitdir: ...' file for worktrees/submodules."""
    g = path / ".git"
    if g.is_file():
        txt = g.read_text().strip()
        if txt.startswith("gitdir:"):
            return (path / txt[len("gitdir:"):].strip()).resolve()
    return g


def web_url(remote: str):
    m = re.match(r"^(?:ssh://)?git@([^:/]+)[:/](.+?)(?:\.git)?/?$", remote)
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    m = re.match(r"^https?://(?:[^@/]+@)?(.+?)(?:\.git)?/?$", remote)
    return f"https://{m.group(1)}" if m else None


def default_branch(path: Path) -> str:
    rc, out, _ = git(path, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if rc == 0 and out.startswith("origin/"):
        return out[len("origin/"):]
    for b in ("main", "master"):
        for ref in (f"refs/heads/{b}", f"refs/remotes/origin/{b}"):
            if git(path, "show-ref", "--verify", "--quiet", ref)[0] == 0:
                return b
    return "main"


def repo_status(path: Path) -> dict:
    s = {"name": repo_id(path), "path": str(path), "branch": None, "detached": False,
         "default": None, "upstream": None, "ahead": 0, "behind": 0, "staged": 0,
         "unstaged": 0, "untracked": 0, "conflicts": 0, "stashes": 0,
         "commit": None, "web": None, "fetchedAt": None, "error": None}
    rc, out, err = git(path, "status", "--porcelain=v2", "--branch")
    if rc:
        s["error"] = err or f"git status failed ({rc})"
        return s
    for line in out.splitlines():
        if line.startswith("# branch.head "):
            head = line[len("# branch.head "):]
            s["detached"] = head == "(detached)"
            s["branch"] = None if s["detached"] else head
        elif line.startswith("# branch.upstream "):
            s["upstream"] = line[len("# branch.upstream "):]
        elif line.startswith("# branch.ab "):
            a, b = line[len("# branch.ab "):].split()
            s["ahead"], s["behind"] = int(a[1:]), int(b[1:])
        elif line[:2] in ("1 ", "2 "):
            xy = line[2:4]
            s["staged"] += xy[0] != "."
            s["unstaged"] += xy[1] != "."
        elif line.startswith("u "):
            s["conflicts"] += 1
        elif line.startswith("? "):
            s["untracked"] += 1

    s["default"] = default_branch(path)
    rc, out, _ = git(path, "log", "-1", "--format=%h%x1f%s%x1f%an%x1f%cr%x1f%ct")
    if rc == 0 and out:
        h, subj, author, rel, ts = out.split("\x1f")
        s["commit"] = {"hash": h, "subject": subj, "author": author, "rel": rel, "ts": int(ts)}
    rc, out, _ = git(path, "stash", "list")
    s["stashes"] = len(out.splitlines()) if rc == 0 and out else 0
    rc, out, _ = git(path, "remote", "get-url", "origin")
    if rc == 0:
        s["web"] = web_url(out)
    fetch_head = git_dir(path) / "FETCH_HEAD"
    if fetch_head.exists():
        s["fetchedAt"] = int(fetch_head.stat().st_mtime)
    return s


def list_branches(path: Path) -> dict:
    rc, out, err = git(path, "for-each-ref", "--sort=-committerdate",
                       "--format=%(refname)%1f%(committerdate:relative)",
                       "refs/heads", "refs/remotes/origin")
    if rc:
        raise RuntimeError(err)
    branches = {}
    for line in out.splitlines():
        ref, rel = line.split("\x1f")
        if ref.startswith("refs/heads/"):
            name = ref[len("refs/heads/"):]
            branches[name] = {"name": name, "local": True, "rel": rel}
        else:
            name = ref[len("refs/remotes/origin/"):]
            if name != "HEAD" and name not in branches:
                branches[name] = {"name": name, "local": False, "rel": rel}
    cur = git(path, "branch", "--show-current")[1]
    return {"current": cur, "branches": list(branches.values())}


# Operations: each takes (path, request_body) and returns a result dict.
def result(kind: str, message: str, output: str = "") -> dict:
    return {"result": kind, "message": message, "output": output}


def from_git(rc, out, err, ok_msg) -> dict:
    text = "\n".join(x for x in (out, err) if x)
    if rc == 0:
        return result("ok", ok_msg, text)
    first = (err or out or f"git exited {rc}").splitlines()[0]
    return result("error", first, text)


def op_fetch(path, _):
    return from_git(*git(path, "fetch", "--all", "--prune"), "fetched")


def op_pull(path, _):
    st = repo_status(path)
    if st["detached"]:
        return result("skipped", "skipped: detached HEAD")
    if not st["upstream"]:
        return result("skipped", f"skipped: '{st['branch']}' has no upstream")
    rc, out, err = git(path, "pull", "--ff-only")
    if rc == 0 and "Already up to date" in out:
        return result("ok", "already up to date", out)
    return from_git(rc, out, err, f"pulled {st['branch']}")


def _switch(path, target: str):
    st = repo_status(path)
    if st["branch"] == target:
        return result("ok", f"already on {target}")
    proceed, why = before_switch(path, st)
    if not proceed:
        return result("skipped", why)
    return from_git(*git(path, "switch", target), f"switched to {target}")


def op_switch_default(path, _):
    return _switch(path, default_branch(path))


def op_checkout(path, body):
    branch = str(body.get("branch", "")).strip()
    branch = branch[len("origin/"):] if branch.startswith("origin/") else branch
    if not branch or branch.startswith("-") or \
            git(path, "check-ref-format", "--branch", branch)[0] != 0:
        return result("error", f"invalid branch name: {branch!r}")
    exists = any(git(path, "show-ref", "--verify", "--quiet", ref)[0] == 0
                 for ref in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"))
    if not exists:
        return result("skipped", f"skipped: no branch '{branch}'")
    return _switch(path, branch)


def op_sync(path, body):
    """Morning sync: fetch, switch to the default branch, fast-forward pull."""
    steps = [op_fetch(path, body)]
    if steps[-1]["result"] == "ok":
        steps.append(op_switch_default(path, body))
    if steps[-1]["result"] == "ok":
        steps.append(op_pull(path, body))
    last = steps[-1]
    output = "\n".join(s["output"] for s in steps if s["output"])
    return result(last["result"], last["message"], output)


def op_stash(path, _):
    st = repo_status(path)
    if not (st["staged"] + st["unstaged"] + st["untracked"] + st["conflicts"]):
        return result("skipped", "nothing to stash")
    msg = f"rcc {time.strftime('%Y-%m-%d %H:%M')}"
    return from_git(*git(path, "stash", "push", "--include-untracked", "-m", msg), "stashed")


def op_stash_pop(path, _):
    if not repo_status(path)["stashes"]:
        return result("skipped", "no stash entries")
    return from_git(*git(path, "stash", "pop"), "stash popped")


def op_prune_merged(path, _):
    d = default_branch(path)
    cur = git(path, "branch", "--show-current")[1]
    rc, out, err = git(path, "branch", "--merged", d, "--format=%(refname:short)")
    if rc:
        return result("error", err)
    victims = [b for b in out.splitlines() if b and b not in (d, cur)]
    if not victims:
        return result("ok", "no merged branches to prune")
    deleted, logs = [], []
    for b in victims:
        rc, o, e = git(path, "branch", "-d", b)  # -d refuses unmerged work
        logs.append(o or e)
        if rc == 0:
            deleted.append(b)
    return result("ok", f"pruned {len(deleted)}: {', '.join(deleted)}", "\n".join(logs))


def make_opener(oid):
    def op(path, _):
        o = OPENERS[oid]
        cmd = [str(c).replace("{path}", str(path)) for c in o["cmd"]]
        subprocess.Popen(cmd, cwd=str(path), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=not IS_WIN)
        return result("ok", f"opened in {o['label']}")
    return op


def set_root(body) -> dict:
    """Switch the folder being managed (from the UI's folder picker)."""
    global ROOT, DEPTH
    p = Path(str(body.get("path", "")).strip() or ".").expanduser()
    p = (p if p.is_absolute() else ROOT / p).resolve()
    if not p.is_dir():
        raise ValueError(f"not a folder: {p}")
    depth = body.get("depth", DEPTH)
    if not isinstance(depth, int) or not 1 <= depth <= 4:
        raise ValueError("depth must be 1-4")
    ROOT, DEPTH = p, depth
    return {"root": str(ROOT), "count": len(discover())}


def overview() -> dict:
    return {"root": str(ROOT), "home": str(Path.home()), "depth": DEPTH, "repos": list(discover()),
            "openers": [{k: o[k] for k in ("id", "label", "short", "kind")} for o in OPENERS.values()]}


OPS = {
    "fetch": op_fetch, "pull": op_pull, "switch_default": op_switch_default,
    "checkout": op_checkout, "sync": op_sync, "stash": op_stash,
    "stash_pop": op_stash_pop, "prune_merged": op_prune_merged,
    **{f"open_{k}": make_opener(k) for k in OPENERS},
}


class Handler(BaseHTTPRequestHandler):
    server_version = "RepoCommandCenter/1.0"

    def log_message(self, *_):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):  # blocks DNS-rebinding attacks
        return (self.headers.get("Host") or "").split(":")[0] in ("127.0.0.1", "localhost")

    def _authed(self):
        return self._host_ok() and secrets.compare_digest(
            self.headers.get("X-RCC-Token", ""), TOKEN)

    def _guard(self, fn):
        try:
            fn()
        except LookupError as e:
            self._send(404, {"error": str(e)})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # surface anything unexpected to the UI log
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden host"})
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            html = (HERE / "index.html").read_text().replace("__RCC_TOKEN__", TOKEN)
            return self._send(200, html.encode(), "text/html; charset=utf-8")
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        repo = (parse_qs(u.query).get("repo") or [""])[0]
        routes = {
            "/api/repos": overview,
            "/api/status": lambda: repo_status(resolve(repo)),
            "/api/branches": lambda: list_branches(resolve(repo)),
        }
        if u.path not in routes:
            return self._send(404, {"error": "not found"})
        self._guard(lambda: self._send(200, routes[u.path]()))

    def do_POST(self):
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        route = urlparse(self.path).path
        if route not in ("/api/op", "/api/root"):
            return self._send(404, {"error": "not found"})

        def run():
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if route == "/api/root":
                return self._send(200, set_root(body))
            op = OPS.get(body.get("op"))
            if not op:
                return self._send(400, {"error": f"unknown op: {body.get('op')}"})
            path = resolve(body.get("repo", ""))
            res = op(path, body)
            res["status"] = repo_status(path)
            self._send(200, res)
        self._guard(run)


def main():
    global ROOT, DEPTH
    ap = argparse.ArgumentParser(description="Repo Command Center - manage all git repos in a folder")
    ap.add_argument("folder", nargs="?", help="folder containing repos (default: current directory)")
    ap.add_argument("--root", help="same as FOLDER (kept for compatibility)")
    ap.add_argument("--depth", type=int, default=1, choices=range(1, 5),
                    help="folder levels to search for repos, e.g. 2 for ~/code/<org>/<repo> (default 1)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    ROOT = Path(args.folder or args.root or os.getcwd()).expanduser().resolve()
    DEPTH = args.depth
    if not ROOT.is_dir():
        sys.exit(f"Not a folder: {ROOT}")
    if not shutil.which("git"):
        sys.exit("git was not found on PATH")

    for port in range(args.port, args.port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    else:
        sys.exit("No free port found")

    url = f"http://127.0.0.1:{port}/"
    tools = ", ".join(o["label"] for o in OPENERS.values()) or "none detected"
    print(f"Repo Command Center: {url}\n  folder: {ROOT} ({len(discover())} repos, depth {DEPTH})"
          f"\n  open-in tools: {tools}\n  Ctrl+C to stop", flush=True)
    if not args.no_browser:
        threading.Timer(0.4, webbrowser.open, (url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
