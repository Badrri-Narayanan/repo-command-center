"""'Open in' tools (editors, file manager, terminal), detected per machine.

Add, relabel or hide tools in ~/.repo-command-center.json, e.g.
    {"openers": [{"id": "sublime", "label": "Sublime Text", "short": "ST", "cmd": ["subl", "{path}"]},
                 {"id": "cursor", "cmd": null}]}
"kind": "editor" shows as a tile button; "system" only appears in the ... menu.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

LINUX_TERMINALS = (
    ("gnome-terminal", "--working-directory={path}"),
    ("konsole", "--workdir", "{path}"),
    ("xfce4-terminal", "--working-directory={path}"),
    ("x-terminal-emulator",),
)


def default_config_file() -> Path:
    return Path.home() / ".repo-command-center.json"


@dataclass(frozen=True)
class Opener:
    id: str
    label: str
    short: str
    kind: str
    cmd: tuple

    @classmethod
    def from_spec(cls, spec: dict) -> "Opener":
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in spec.items() if k in known}
        return cls(**{**values, "cmd": tuple(spec["cmd"])})

    def command_for(self, path: Path) -> List[str]:
        return [str(part).replace("{path}", str(path)) for part in self.cmd]

    def public(self) -> dict:
        return {"id": self.id, "label": self.label, "short": self.short, "kind": self.kind}


class Platform:
    """What's installed on this machine. Every probe is injectable, so tests can fake any OS."""

    def __init__(self, system: str = sys.platform, os_name: str = os.name,
                 which: Callable[[str], Optional[str]] = shutil.which,
                 app_dirs: Optional[Iterable[Path]] = None):
        self.is_mac = system == "darwin"
        self.is_windows = os_name == "nt"
        self.which = which
        self.app_dirs = tuple(app_dirs) if app_dirs is not None else (
            Path("/Applications"), Path.home() / "Applications")

    def mac_app(self, *names: str) -> Optional[str]:
        return next((n for n in names for d in self.app_dirs if (d / f"{n}.app").exists()), None)

    def editor(self, cli: str, *mac_apps: str) -> Optional[list]:
        """Prefer the CLI shim on PATH; on macOS fall back to the .app bundle."""
        exe = self.which(cli)
        if exe:
            return [exe, "{path}"]
        app = self.mac_app(*mac_apps) if self.is_mac else None
        return ["open", "-a", app, "{path}"] if app else None

    def file_manager(self) -> Optional[list]:
        if self.is_mac:
            return ["open", "{path}"]
        if self.is_windows:
            return ["explorer", "{path}"]
        return ["xdg-open", "{path}"] if self.which("xdg-open") else None

    def terminal(self) -> Optional[list]:
        if self.is_mac:
            return ["open", "-a", self.mac_app("iTerm") or "Terminal", "{path}"]
        if self.is_windows:
            return self._windows_terminal()
        return next((list(cmd) for cmd in LINUX_TERMINALS if self.which(cmd[0])), None)

    def _windows_terminal(self) -> list:
        if self.which("wt"):
            return ["wt", "-d", "{path}"]
        return ["cmd", "/c", "start", "cmd", "/K", "cd", "/d", "{path}"]


class OpenerRegistry:
    def __init__(self, openers: Iterable[Opener] = ()):
        self._by_id: Dict[str, Opener] = {o.id: o for o in openers}

    def __iter__(self):
        return iter(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)

    def get(self, opener_id: str) -> Optional[Opener]:
        return self._by_id.get(opener_id)

    def public(self) -> List[dict]:
        return [o.public() for o in self]

    @classmethod
    def detect(cls, platform: Optional[Platform] = None, config_file: Optional[Path] = None,
               warn: Callable[[str], None] = print) -> "OpenerRegistry":
        specs = cls.builtin_specs(platform or Platform())
        specs = cls.apply_overrides(specs, config_file or default_config_file(), warn)
        return cls(Opener.from_spec(spec) for spec in specs.values() if spec.get("cmd"))

    @staticmethod
    def builtin_specs(p: Platform) -> Dict[str, dict]:
        rows = [
            ("vscode", "VS Code", "VS", "editor", p.editor("code", "Visual Studio Code")),
            ("cursor", "Cursor", "Cu", "editor", p.editor("cursor", "Cursor")),
            ("intellij", "IntelliJ IDEA", "IJ", "editor",
             p.editor("idea", "IntelliJ IDEA", "IntelliJ IDEA Ultimate", "IntelliJ IDEA CE")),
            ("files", "Finder" if p.is_mac else "File manager", "📁", "system", p.file_manager()),
            ("terminal", "Terminal", ">_", "system", p.terminal()),
        ]
        return {r[0]: dict(zip(("id", "label", "short", "kind", "cmd"), r)) for r in rows}

    @classmethod
    def apply_overrides(cls, specs: Dict[str, dict], config_file: Path, warn) -> Dict[str, dict]:
        try:
            overrides = json.loads(config_file.read_text()).get("openers", [])
            return cls._merge(specs, overrides)
        except FileNotFoundError:
            return specs
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            warn(f"warning: ignoring {config_file}: {e}")
            return specs

    @staticmethod
    def _merge(specs: Dict[str, dict], overrides: List[dict]) -> Dict[str, dict]:
        merged = dict(specs)
        for override in overrides:
            oid = override["id"]
            base = merged.get(oid, {"id": oid, "label": oid, "short": oid[:2].upper(), "kind": "editor"})
            merged[oid] = {**base, **override}
        return merged
