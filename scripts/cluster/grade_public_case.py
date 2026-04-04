#!/usr/bin/env python3
"""Grade a coarse public comparison report for case-study readiness."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cluster.evaluation import grade_public_case_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Grade a public comparison report against the public case rubric")
    parser.add_argument("--report", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--trace", help="Optional replay trace path; required for replay-ready PASS")
    parser.add_argument("--output", help="Optional JSON output path")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when the public case grade fails")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    grade = grade_public_case_report(args.report, labels=args.labels, trace=args.trace)
    body = json.dumps(grade, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(body, encoding="utf-8")
        print(f"wrote public case grade to {output_path}")
    else:
        print(body)
    return 1 if args.strict and grade["status"] != "pass" else 0


if __name__ == "__main__":
    raise SystemExit(main())
