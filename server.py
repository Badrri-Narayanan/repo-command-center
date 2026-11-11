#!/usr/bin/env python3
"""Repo Command Center - a local web dashboard for every git repo in a folder.

Usage (macOS, Linux, Windows; Python 3.9+ and git, no other dependencies):
    python3 /path/to/server.py [FOLDER] [--depth N] [--port 8765] [--no-browser]

The code lives in the `rcc` package next to this file; this is only the launcher.
"""
from rcc.cli import main

if __name__ == "__main__":
    main()
