#!/usr/bin/env python3
"""Render a markdown proof summary for a transit calibration report."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.transit.calibration import (
    build_transit_calibration_report,
    build_transit_calibration_suite_report,
    render_transit_calibration_markdown,
    render_transit_calibration_suite_markdown,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render a markdown summary for a transit calibration report")
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--output")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    labels_path = Path(args.labels)
    if labels_path.is_dir():
        report = build_transit_calibration_suite_report(args.archive_root, labels_path)
        body = render_transit_calibration_suite_markdown(report)
    else:
        report = build_transit_calibration_report(args.archive_root, labels_path)
        body = render_transit_calibration_markdown(report)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(body, encoding="utf-8")
        print(f"wrote transit calibration markdown to {output_path}")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
