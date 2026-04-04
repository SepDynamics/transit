#!/usr/bin/env python3
"""Build and optionally persist a transit calibration comparison report."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.transit.calibration import build_transit_calibration_report, build_transit_calibration_suite_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a transit calibration report from archived snapshots and labels")
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--output")
    parser.add_argument("--strict", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    labels_path = Path(args.labels)
    report = (
        build_transit_calibration_suite_report(args.archive_root, labels_path)
        if labels_path.is_dir()
        else build_transit_calibration_report(args.archive_root, labels_path)
    )
    body = json.dumps(report, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(body, encoding="utf-8")
        print(f"wrote transit calibration report to {output_path}")
    else:
        print(body)
    return 1 if args.strict and not bool((report.get("comparison") or {}).get("value_case_supported")) else 0


if __name__ == "__main__":
    raise SystemExit(main())
