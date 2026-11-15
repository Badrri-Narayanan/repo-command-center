import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from helpers import Fixture, git
from rcc.gitcmd import Git
from rcc.openers import Opener, OpenerRegistry
from rcc.operations import Operations
from rcc.web import App, NoFreePortError, RequestHandler, StaticAssets, make_server
from rcc.workspace import Workspace

APP_DIR = Path(__file__).resolve().parent.parent


class TestStaticAssets(unittest.TestCase):
    def setUp(self):
        self.assets = StaticAssets(APP_DIR / "static")

    def test_serves_js_and_css_in_subfolders(self):
        body, ctype = self.assets.find("/static/js/main.js")
        self.assertEqual(ctype, "text/javascript; charset=utf-8")
        self.assertIn(b"import", body)
        self.assertEqual(self.assets.find("/static/css/app.css")[1], "text/css; charset=utf-8")

    def test_refuses_everything_else(self):
        for path in ("/static/../server.py", "/static/../rcc/web.py", "/static/js/nope.js",
                     "/static/js", "/server.py", "/static/%2e%2e/server.py", "/static/../../etc/passwd"):
            with self.subTest(path=path):
                self.assertIsNone(self.assets.find(path))


class TestInvoke(unittest.TestCase):
    def test_maps_exceptions_to_status_codes(self):
        def raises(exc):
            def route():
                raise exc
            return route

        self.assertEqual(RequestHandler.invoke(lambda: {"a": 1}), (200, {"a": 1}))
        self.assertEqual(RequestHandler.invoke(raises(LookupError("x")))[0], 404)
        self.assertEqual(RequestHandler.invoke(raises(ValueError("x")))[0], 400)
        self.assertEqual(RequestHandler.invoke(raises(RuntimeError("kaboom"))),
                         (500, {"error": "RuntimeError: kaboom"}))


class TestMakeServer(unittest.TestCase):
    def test_tries_next_port_then_gives_up(self):
        bound = mock.Mock(server_address=("127.0.0.1", 9001))
        server_cls = mock.Mock(side_effect=[OSError, bound])
        self.assertEqual(make_server(mock.Mock(), 9000, server_cls=server_cls), (bound, 9001))
        with self.assertRaises(NoFreePortError):
            make_server(mock.Mock(), 9000, attempts=3, server_cls=mock.Mock(side_effect=OSError))


class TestHttpApi(unittest.TestCase):
    """End-to-end over a real socket against real repos."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fx = Fixture(Path(cls._tmp.name).resolve())
        cls.fx.remote_repo("svc")
        cls.launched = []
        openers = OpenerRegistry([Opener("tool", "Tool", "T", "editor", ("tool", "{path}"))])
        ops = Operations(openers=openers, launcher=lambda cmd, cwd: cls.launched.append(cmd))
        cls.workspace = Workspace(cls.fx.root, 1, Git())
        cls.app = App(cls.workspace, ops, openers, APP_DIR, token="test-token")
        cls.server, cls.port = make_server(cls.app, 0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    def setUp(self):
        self.workspace.change_root(str(self.fx.root), 1)

    def call(self, method, path, body=None, token="test-token", host="127.0.0.1"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Host": f"{host}:{self.port}", "Content-Type": "application/json"}
        if token:
            headers["X-RCC-Token"] = token
        conn.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        is_json = response.getheader("Content-Type") == "application/json"
        return response.status, json.loads(raw) if is_json else raw.decode()

    def test_index_page_embeds_token_and_assets(self):
        status, html = self.call("GET", "/", token=None)
        self.assertEqual(status, 200)
        self.assertIn('<meta name="rcc-token" content="test-token">', html)
        self.assertIn('src="static/js/main.js"', html)
        self.assertIn('href="static/css/app.css"', html)

    def test_static_assets_need_no_token(self):
        self.assertEqual(self.call("GET", "/static/js/model.js", token=None)[0], 200)

    def test_rejects_foreign_host_header(self):
        self.assertEqual(self.call("GET", "/", host="evil.example")[0], 403)
        self.assertEqual(self.call("POST", "/api/op", {"op": "fetch", "repo": "svc"}, host="evil.example")[0], 401)

    def test_requires_valid_token(self):
        for token in (None, "wrong"):
            with self.subTest(token=token):
                self.assertEqual(self.call("GET", "/api/repos", token=token)[0], 401)
                self.assertEqual(self.call("POST", "/api/op", {"op": "fetch", "repo": "svc"}, token=token)[0], 401)
        self.assertEqual(self.call("GET", "/static/../server.py", token=None)[0], 401)

    def test_overview(self):
        status, data = self.call("GET", "/api/repos")
        self.assertEqual((status, data["repos"], data["depth"]), (200, ["svc"], 1))
        self.assertEqual(data["openers"], [{"id": "tool", "label": "Tool", "short": "T", "kind": "editor"}])

    def test_status_and_branches(self):
        status, data = self.call("GET", "/api/status?repo=svc")
        self.assertEqual((status, data["branch"], data["default"]), (200, "main", "main"))
        status, data = self.call("GET", "/api/branches?repo=svc")
        self.assertEqual((status, data["current"]), (200, "main"))

    def test_explorer_routes(self):
        path = self.fx.root / "svc"
        (path / "README.md").write_text("changed\n")
        git(path, "tag", "v9")
        try:
            status, log = self.call("GET", "/api/log?repo=svc&range=all&limit=5")
            self.assertEqual((status, log["commits"][0]["subject"]), (200, "initial"))
            rev = log["commits"][0]["hash"]
            self.assertEqual(self.call("GET", f"/api/commit?repo=svc&rev={rev}")[1]["files"][0]["path"], "README.md")
            self.assertEqual(self.call("GET", "/api/changes?repo=svc")[1]["files"][0]["path"], "README.md")
            self.assertIn("+changed", self.call("GET", "/api/diff?repo=svc&path=README.md&staged=0")[1]["patch"])
            self.assertEqual(self.call("GET", "/api/file?repo=svc&path=README.md&rev=HEAD")[1]["content"], "hello\n")
            self.assertEqual(self.call("GET", "/api/tags?repo=svc")[1]["tags"][0]["name"], "v9")
            author = self.call("GET", "/api/authors?repo=svc")[1]["authors"][0]
            self.assertEqual(self.call("GET", f"/api/log?repo=svc&author={author['email']}")[1]["commits"][0]["hash"], rev)
            git(path, "stash", "push", "-q")
            self.assertEqual(self.call("GET", "/api/stashes?repo=svc")[1]["stashes"][0]["index"], 0)
            self.assertIn("+changed", self.call("GET", "/api/stash?repo=svc&index=0")[1]["patch"])
            status, data = self.call("POST", "/api/op", {"op": "stash_drop", "repo": "svc", "index": 0})
            self.assertEqual((status, data["result"], data["status"]["stashes"]), (200, "ok", 0))
        finally:
            git(path, "checkout", "-q", "--", ".")
            git(path, "tag", "-d", "v9")

    def test_explorer_routes_reject_bad_input(self):
        self.assertEqual(self.call("GET", "/api/commit?repo=svc&rev=--all")[0], 400)
        self.assertEqual(self.call("GET", "/api/log?repo=svc&range=nope")[0], 400)
        self.assertEqual(self.call("GET", "/api/log?repo=svc&author=%3Cx%3E")[0], 400)
        self.assertEqual(self.call("GET", "/api/file?repo=svc&path=../../etc/passwd")[0], 404)
        self.assertEqual(self.call("GET", "/api/diff?repo=svc&path=README.md")[0], 404)
        self.assertEqual(self.call("GET", "/api/log?repo=../x")[0], 404)

    def test_unknown_routes_and_repos(self):
        self.assertEqual(self.call("GET", "/api/nope")[0], 404)
        self.assertEqual(self.call("GET", "/api/status?repo=../../etc")[0], 404)
        self.assertEqual(self.call("POST", "/api/nope", {})[0], 404)

    def test_run_operation_returns_fresh_status(self):
        status, data = self.call("POST", "/api/op", {"op": "fetch", "repo": "svc"})
        self.assertEqual((status, data["result"], data["status"]["name"]), (200, "ok", "svc"))
        self.assertIsNotNone(data["status"]["fetchedAt"])

    def test_open_operation(self):
        self.assertEqual(self.call("POST", "/api/op", {"op": "open_tool", "repo": "svc"})[1]["message"], "opened in Tool")
        self.assertEqual(self.launched[-1], ["tool", str(self.fx.root / "svc")])

    def test_unknown_operation_is_checked_before_repo(self):
        status, data = self.call("POST", "/api/op", {"op": "rm -rf", "repo": "does-not-exist"})
        self.assertEqual(status, 400)
        self.assertIn("unknown op", data["error"])

    def test_bad_json_body(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request("POST", "/api/op", body="{nope", headers={"X-RCC-Token": "test-token"})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()

    def test_change_root(self):
        self.assertEqual(self.call("POST", "/api/root", {"path": "/definitely/not/here"})[0], 400)
        status, data = self.call("POST", "/api/root", {"path": str(self.fx.root), "depth": 2})
        self.assertEqual((status, data["count"]), (200, 1))

    def test_unexpected_errors_become_500(self):
        with mock.patch.object(Workspace, "resolve", side_effect=RuntimeError("kaboom")):
            status, data = self.call("GET", "/api/status?repo=svc")
        self.assertEqual((status, data["error"]), (500, "RuntimeError: kaboom"))


if __name__ == "__main__":
    unittest.main()
