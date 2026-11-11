"""Command-line entry point: parse arguments, wire the app together, serve."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import threading
import webbrowser
from pathlib import Path
from typing import List, Optional

from .openers import OpenerRegistry
from .operations import Operations
from .web import App, NoFreePortError, make_server
from .workspace import Workspace

APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 8765


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repo Command Center - manage all git repos in a folder")
    parser.add_argument("folder", nargs="?", help="folder containing repos (default: current directory)")
    parser.add_argument("--root", help="same as FOLDER (kept for compatibility)")
    parser.add_argument("--depth", type=int, default=1, choices=range(Workspace.MIN_DEPTH, Workspace.MAX_DEPTH + 1),
                        help="folder levels to search for repos, e.g. 2 for ~/code/<org>/<repo> (default 1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args(argv)


def resolve_root(args: argparse.Namespace) -> Path:
    root = Path(args.folder or args.root or os.getcwd()).expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"Not a folder: {root}")
    if not shutil.which("git"):
        sys.exit("git was not found on PATH")
    return root


def build_app(root: Path, depth: int) -> App:
    openers = OpenerRegistry.detect()
    workspace = Workspace(root, depth, exclude=APP_DIR)
    return App(workspace, Operations(openers=openers), openers, APP_DIR)


def banner(app: App, url: str) -> str:
    tools = ", ".join(o.label for o in app.openers) or "none detected"
    ws = app.workspace
    return (f"Repo Command Center: {url}\n  folder: {ws.root} ({len(ws.discover())} repos, depth {ws.depth})"
            f"\n  open-in tools: {tools}\n  Ctrl+C to stop")


def serve(server):
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        server.server_close()


def main(argv: Optional[List[str]] = None, server_factory=make_server, open_browser=webbrowser.open):
    args = parse_args(argv)
    app = build_app(resolve_root(args), args.depth)
    try:
        server, port = server_factory(app, args.port)
    except NoFreePortError:
        sys.exit("No free port found")
    url = f"http://127.0.0.1:{port}/"
    print(banner(app, url), flush=True)
    if not args.no_browser:
        threading.Timer(0.4, open_browser, (url,)).start()
    serve(server)
