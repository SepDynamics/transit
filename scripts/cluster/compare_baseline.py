#!/usr/bin/env python3
"""Compare Sentinel scoring against threshold rules on the same trace."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cluster.evaluation import build_comparison_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare Sentinel vs threshold baseline on a trace")
    parser.add_argument("--trace", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--window-samples", type=int, default=6)
    parser.add_argument("--baseline-persistence-windows", type=int, default=3)
    parser.add_argument("--episode-cooldown-minutes", type=int, default=0)
    parser.add_argument("--output", help="Optional JSON report output path")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = build_comparison_report(
        args.trace,
        args.labels,
        window_samples=max(6, int(args.window_samples)),
        persistence_windows=max(1, int(args.baseline_persistence_windows)),
        episode_cooldown_ms=max(0, int(args.episode_cooldown_minutes)) * 60 * 1000,
    )
    body = json.dumps(report, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(body, encoding="utf-8")
        print(f"wrote comparison report to {output_path}")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
