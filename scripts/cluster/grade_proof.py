#!/usr/bin/env python3
"""Grade a comparison report against the first-proof wedge criteria."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cluster.evaluation import grade_comparison_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Grade a comparison report against the proof criteria")
    parser.add_argument("--report", required=True)
    parser.add_argument("--trace", help="Optional replay trace path; required for replay-ready PASS")
    parser.add_argument("--output", help="Optional JSON output path")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when the proof grade fails")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    grade = grade_comparison_report(args.report, trace=args.trace)
    body = json.dumps(grade, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(body, encoding="utf-8")
        print(f"wrote proof grade to {output_path}")
    else:
        print(body)
    return 1 if args.strict and grade["status"] != "pass" else 0


if __name__ == "__main__":
    raise SystemExit(main())
