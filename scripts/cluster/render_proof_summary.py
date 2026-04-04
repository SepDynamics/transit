#!/usr/bin/env python3
"""Render a concise markdown proof summary from a comparison report."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cluster.evaluation import grade_comparison_report, render_comparison_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render a one-page markdown summary from a comparison report")
    parser.add_argument("--report", required=True)
    parser.add_argument("--trace", help="Optional trace path for replay-ready grading and summary context")
    parser.add_argument("--labels", help="Optional labels path for summary context")
    parser.add_argument("--replay-command", help="Optional replay command to embed in the markdown")
    parser.add_argument("--output", help="Optional markdown output path")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    grade = grade_comparison_report(args.report, trace=args.trace)
    body = render_comparison_markdown(
        args.report,
        grade=grade,
        trace_path=args.trace,
        labels_path=args.labels,
        replay_command=args.replay_command,
    )
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(body, encoding="utf-8")
        print(f"wrote markdown summary to {output_path}")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
