#!/usr/bin/env python3
"""Render a concise markdown summary for a quiet public control case."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cluster.evaluation import grade_public_control_report, render_public_control_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render a one-page markdown summary for a public control report")
    parser.add_argument("--report", required=True)
    parser.add_argument("--trace", help="Optional trace path for replay-ready grading and summary context")
    parser.add_argument("--labels", help="Optional labels path for summary context")
    parser.add_argument("--replay-command", help="Optional replay command to embed in the markdown")
    parser.add_argument("--output", help="Optional markdown output path")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    grade = grade_public_control_report(args.report, trace=args.trace)
    body = render_public_control_markdown(
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
        print(f"wrote public control markdown to {output_path}")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
