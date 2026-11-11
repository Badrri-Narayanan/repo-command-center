import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from rcc import cli
from rcc.web import NoFreePortError


class TestParseArgs(unittest.TestCase):
    def test_defaults(self):
        args = cli.parse_args([])
        self.assertEqual((args.folder, args.depth, args.port, args.no_browser), (None, 1, 8765, False))

    def test_rejects_depth_out_of_range(self):
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr"), self.assertRaises(SystemExit):
            cli.parse_args(["--depth", "9"])


class TestMain(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name).resolve()
        self.server = mock.Mock()
        self.server.serve_forever.side_effect = KeyboardInterrupt
        self.factory_calls = []

    def tearDown(self):
        self._tmp.cleanup()

    def factory(self, app, port):
        self.factory_calls.append((app, port))
        return self.server, port + 1

    def run_main(self, argv, factory=None):
        out = io.StringIO()
        with mock.patch.object(cli.threading, "Timer") as timer, redirect_stdout(out):
            cli.main(argv, server_factory=factory or self.factory)
        return out.getvalue(), timer

    def test_starts_server_and_opens_browser(self):
        out, timer = self.run_main([str(self.folder), "--depth", "2", "--port", "9100"])
        app, port = self.factory_calls[0]
        self.assertEqual((app.workspace.root, app.workspace.depth, port), (self.folder, 2, 9100))
        self.assertIn("http://127.0.0.1:9101/", out)
        self.assertIn("0 repos, depth 2", out)
        self.assertIn("bye", out)
        timer.assert_called_once()
        self.server.server_close.assert_called_once()

    def test_no_browser_and_root_flag(self):
        _, timer = self.run_main(["--root", str(self.folder), "--no-browser"])
        self.assertEqual(self.factory_calls[0][0].workspace.root, self.folder)
        timer.assert_not_called()

    def test_exits_on_bad_folder_missing_git_or_no_port(self):
        with self.assertRaises(SystemExit):
            self.run_main([str(self.folder / "missing")])
        with mock.patch.object(cli.shutil, "which", return_value=None), self.assertRaises(SystemExit):
            self.run_main([str(self.folder)])
        with self.assertRaises(SystemExit):
            self.run_main([str(self.folder)], factory=mock.Mock(side_effect=NoFreePortError))

    def test_app_excludes_its_own_folder(self):
        app = cli.build_app(self.folder, 1)
        self.assertEqual(app.workspace._exclude, cli.APP_DIR)


if __name__ == "__main__":
    unittest.main()
