#!/usr/bin/env python3
"""Compatibility wrapper for transit calibration summary rendering."""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.transit.render_calibration_summary import build_parser, main

__all__ = [
    "build_parser",
    "main",
]

if __name__ == "__main__":
    raise SystemExit(main())
