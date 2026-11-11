import json
import tempfile
import unittest
from pathlib import Path

from rcc.openers import Opener, OpenerRegistry, Platform, default_config_file


def fake_platform(system="linux", os_name="posix", available=(), app_dirs=()):
    which = lambda name: f"/bin/{name}" if name in available else None  # noqa: E731
    return Platform(system=system, os_name=os_name, which=which, app_dirs=app_dirs)


class TestOpener(unittest.TestCase):
    def test_command_substitutes_path(self):
        opener = Opener("t", "Tool", "T", "editor", ("tool", "--dir={path}", "{path}"))
        self.assertEqual(opener.command_for(Path("/r")), ["tool", f"--dir={Path('/r')}", str(Path("/r"))])

    def test_from_spec_ignores_unknown_keys(self):
        opener = Opener.from_spec({"id": "t", "label": "T", "short": "T", "kind": "editor",
                                   "cmd": ["t"], "colour": "red"})
        self.assertEqual(opener.cmd, ("t",))
        self.assertEqual(opener.public(), {"id": "t", "label": "T", "short": "T", "kind": "editor"})


class TestPlatform(unittest.TestCase):
    def test_editor_prefers_cli(self):
        self.assertEqual(fake_platform(available=("code",)).editor("code", "VS"), ["/bin/code", "{path}"])

    def test_editor_falls_back_to_mac_app(self):
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / "Applications" / "Fancy.app").mkdir(parents=True)
            mac = fake_platform(system="darwin", app_dirs=[Path(home) / "Applications"])
            self.assertEqual(mac.editor("fancy", "Nope", "Fancy"), ["open", "-a", "Fancy", "{path}"])
            self.assertIsNone(mac.editor("fancy", "Nope"))
        self.assertIsNone(fake_platform().editor("fancy", "Fancy"))

    def test_file_manager(self):
        self.assertEqual(fake_platform(system="darwin").file_manager(), ["open", "{path}"])
        self.assertEqual(fake_platform(os_name="nt").file_manager(), ["explorer", "{path}"])
        self.assertEqual(fake_platform(available=("xdg-open",)).file_manager(), ["xdg-open", "{path}"])
        self.assertIsNone(fake_platform().file_manager())

    def test_terminal(self):
        self.assertEqual(fake_platform(system="darwin").terminal(), ["open", "-a", "Terminal", "{path}"])
        self.assertEqual(fake_platform(os_name="nt", available=("wt",)).terminal(), ["wt", "-d", "{path}"])
        self.assertEqual(fake_platform(os_name="nt").terminal()[:2], ["cmd", "/c"])
        self.assertEqual(fake_platform(available=("konsole",)).terminal(), ["konsole", "--workdir", "{path}"])
        self.assertIsNone(fake_platform().terminal())


class TestOpenerRegistry(unittest.TestCase):
    def detect(self, config=None, platform=None):
        warnings = []
        with tempfile.TemporaryDirectory() as folder:
            config_file = Path(folder) / "config.json"
            if config is not None:
                config_file.write_text(config if isinstance(config, str) else json.dumps(config))
            registry = OpenerRegistry.detect(platform or fake_platform(system="darwin", available=("code",)),
                                             config_file, warnings.append)
        return registry, warnings

    def test_detects_only_installed_tools(self):
        registry, warnings = self.detect()
        self.assertEqual([o.id for o in registry], ["vscode", "files", "terminal"])
        self.assertEqual((len(registry), warnings), (3, []))
        self.assertEqual(registry.get("files").label, "Finder")
        self.assertIsNone(registry.get("cursor"))

    def test_config_adds_overrides_and_hides(self):
        registry, _ = self.detect({"openers": [
            {"id": "sublime", "cmd": ["subl", "{path}"]},
            {"id": "terminal", "label": "My Term"},
            {"id": "vscode", "cmd": None},
        ]})
        self.assertIsNone(registry.get("vscode"))
        self.assertEqual(registry.get("terminal").label, "My Term")
        self.assertEqual(registry.get("terminal").cmd, ("open", "-a", "Terminal", "{path}"))
        self.assertEqual((registry.get("sublime").short, registry.get("sublime").kind), ("SU", "editor"))

    def test_invalid_config_warns_and_keeps_builtins(self):
        for bad in ("{not json", {"openers": [{"label": "no id"}]}, ["not", "an", "object"]):
            with self.subTest(config=bad):
                registry, warnings = self.detect(bad)
                self.assertIn("warning: ignoring", warnings[0])
                self.assertIsNotNone(registry.get("vscode"))

    def test_public_view_has_no_commands(self):
        registry, _ = self.detect()
        self.assertTrue(all("cmd" not in o for o in registry.public()))

    def test_defaults(self):
        self.assertEqual(Platform().app_dirs[0], Path("/Applications"))
        self.assertEqual(default_config_file().name, ".repo-command-center.json")
        self.assertIsInstance(OpenerRegistry.detect(config_file=Path("/nonexistent/x.json")), OpenerRegistry)


if __name__ == "__main__":
    unittest.main()
