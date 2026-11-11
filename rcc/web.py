"""HTTP layer: the application service, static assets and the request handler."""
from __future__ import annotations

import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .openers import OpenerRegistry
from .operations import Operations
from .workspace import Workspace

JSON_TYPE = "application/json"
HTML_TYPE = "text/html; charset=utf-8"
LOCAL_HOSTS = ("127.0.0.1", "localhost")


class NoFreePortError(RuntimeError):
    pass


class StaticAssets:
    """Serves .js/.css files under one folder. Anything resolving outside it is refused."""

    CONTENT_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}

    def __init__(self, folder: Path, url_prefix: str = "/static/"):
        self.folder = folder.resolve()
        self.url_prefix = url_prefix

    def find(self, url_path: str) -> Optional[Tuple[bytes, str]]:
        if not url_path.startswith(self.url_prefix):
            return None
        target = (self.folder / url_path[len(self.url_prefix):]).resolve()
        if not self._servable(target):
            return None
        return target.read_bytes(), self.CONTENT_TYPES[target.suffix]

    def _servable(self, target: Path) -> bool:
        inside = self.folder in target.parents
        return inside and target.suffix in self.CONTENT_TYPES and target.is_file()


class App:
    """Everything the HTTP API can do, independent of HTTP."""

    TOKEN_PLACEHOLDER = "__RCC_TOKEN__"

    def __init__(self, workspace: Workspace, operations: Operations, openers: OpenerRegistry,
                 base_dir: Path, token: Optional[str] = None):
        self.workspace = workspace
        self.operations = operations
        self.openers = openers
        self.token = token or secrets.token_urlsafe(24)
        self.assets = StaticAssets(base_dir / "static")
        self._index = base_dir / "index.html"

    def index_html(self) -> bytes:
        return self._index.read_text(encoding="utf-8").replace(self.TOKEN_PLACEHOLDER, self.token).encode()

    def overview(self) -> dict:
        return {"root": str(self.workspace.root), "home": str(Path.home()), "depth": self.workspace.depth,
                "repos": list(self.workspace.discover()), "openers": self.openers.public()}

    def status(self, name: str) -> dict:
        return self.workspace.resolve(name).status().to_dict()

    def branches(self, name: str) -> dict:
        return self.workspace.resolve(name).branches()

    def run_op(self, body: dict) -> dict:
        action = self.operations.lookup(body.get("op"))
        repo = self.workspace.resolve(body.get("repo", ""))
        result = action(repo, body)
        return {**result.to_dict(), "status": repo.status().to_dict()}

    def change_root(self, body: dict) -> dict:
        return self.workspace.change_root(body.get("path", ""), body.get("depth"))


Route = Callable[[], dict]


class RequestHandler(BaseHTTPRequestHandler):
    """Bound to an App by make_server(). Every /api call needs the token and a local Host header."""

    server_version = "RepoCommandCenter/2.0"
    app: App

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if not self._host_allowed():
            return self._send_json(403, {"error": "forbidden host"})
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            return self._send(200, self.app.index_html(), HTML_TYPE)
        asset = self.app.assets.find(url.path)
        if asset:
            return self._send(200, *asset)
        self._handle_api(url.path, self._get_routes(parse_qs(url.query)))

    def do_POST(self):
        routes = {
            "/api/op": lambda: self.app.run_op(self._read_json()),
            "/api/root": lambda: self.app.change_root(self._read_json()),
        }
        self._handle_api(urlparse(self.path).path, routes)

    def _get_routes(self, query: dict) -> Dict[str, Route]:
        repo = (query.get("repo") or [""])[0]
        return {
            "/api/repos": self.app.overview,
            "/api/status": lambda: self.app.status(repo),
            "/api/branches": lambda: self.app.branches(repo),
        }

    def _handle_api(self, path: str, routes: Dict[str, Route]):
        if not self._authorized():
            return self._send_json(401, {"error": "unauthorized"})
        route = routes.get(path)
        if route is None:
            return self._send_json(404, {"error": "not found"})
        self._send_json(*self.invoke(route))

    @staticmethod
    def invoke(route: Route) -> Tuple[int, dict]:
        try:
            return 200, route()
        except LookupError as e:
            return 404, {"error": str(e)}
        except ValueError as e:
            return 400, {"error": str(e)}
        except Exception as e:  # surfaced in the UI's activity log rather than lost
            return 500, {"error": f"{type(e).__name__}: {e}"}

    def _host_allowed(self) -> bool:  # blocks DNS-rebinding attacks
        return (self.headers.get("Host") or "").split(":")[0] in LOCAL_HOSTS

    def _authorized(self) -> bool:
        token = self.headers.get("X-RCC-Token", "")
        return self._host_allowed() and secrets.compare_digest(token, self.app.token)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def _send_json(self, code: int, payload: dict):
        self._send(code, json.dumps(payload).encode(), JSON_TYPE)

    def _send(self, code: int, data: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def make_server(app: App, port: int, attempts: int = 20, server_cls=ThreadingHTTPServer):
    """Bind to the first free port from `port` upwards. Returns (server, actual_port)."""
    handler = type("BoundRequestHandler", (RequestHandler,), {"app": app})
    for candidate in range(port, port + attempts):
        try:
            server = server_cls(("127.0.0.1", candidate), handler)
        except OSError:
            continue
        return server, server.server_address[1]
    raise NoFreePortError(f"no free port in {port}-{port + attempts - 1}")
