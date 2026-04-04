#!/usr/bin/env python3
"""Cache and rank public GWDG incident traces for exploratory Sentinel evaluation."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cluster.evaluation import build_comparison_report, load_labels
from scripts.cluster.gwdg_dataset import (
    DEFAULT_GWDG_ARCHIVE_NAME,
    DEFAULT_GWDG_DOI,
    build_label_payload_from_gwdg_archive,
    build_trace_from_gwdg_archive,
    download_gwdg_dataset,
    list_gwdg_telemetry_members,
)
from scripts.cluster.trace_utils import load_trace_file, write_trace_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sweep and rank public GWDG incident traces")
    parser.add_argument(
        "--dataset",
        default=str(Path("output") / "public" / DEFAULT_GWDG_ARCHIVE_NAME),
        help="Path to the local GWDG Zenodo zip archive",
    )
    parser.add_argument("--download", action="store_true", help="Download the archive from Zenodo if it is missing")
    parser.add_argument(
        "--cache-dir",
        default=str(Path("output") / "public" / "gwdg"),
        help="Directory for cached traces, labels, reports, and the summary JSON",
    )
    parser.add_argument("--refresh", action="store_true", help="Rebuild cached traces, labels, and reports")
    parser.add_argument("--window-samples", type=int, default=6)
    parser.add_argument("--baseline-persistence-windows", type=int, default=3)
    parser.add_argument("--episode-cooldown-minutes", type=int, default=240)
    parser.add_argument("--output", help="Optional summary JSON output path")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        if not args.download:
            raise FileNotFoundError(f"dataset archive not found: {dataset_path}")
        download_gwdg_dataset(dataset_path)
        print(f"downloaded public GWDG dataset to {dataset_path}")

    cache_dir = Path(args.cache_dir)
    traces_dir = cache_dir / "traces"
    labels_dir = cache_dir / "labels"
    reports_dir = cache_dir / "reports"
    for directory in (traces_dir, labels_dir, reports_dir):
        directory.mkdir(parents=True, exist_ok=True)

    summary_rows: List[Dict[str, Any]] = []
    cooldown_ms = max(0, int(args.episode_cooldown_minutes)) * 60 * 1000
    for member in list_gwdg_telemetry_members(dataset_path):
        stem = Path(member).name.removesuffix("_tidy.csv.bz2")
        trace_path = traces_dir / f"{stem}.json"
        labels_path = labels_dir / f"{stem}.json"
        report_path = reports_dir / f"{stem}.json"
        trace_id = f"gwdg-{stem}"

        if args.refresh or not trace_path.exists():
            trace = build_trace_from_gwdg_archive(
                dataset_path,
                telemetry_selector=stem,
                trace_id=trace_id,
            )
            write_trace_file(trace_path, trace)
        else:
            trace = load_trace_file(trace_path)

        if args.refresh or not labels_path.exists():
            labels = build_label_payload_from_gwdg_archive(
                dataset_path,
                telemetry_selector=stem,
                trace=trace,
            )
            labels_path.write_text(json.dumps(labels, indent=2), encoding="utf-8")
        else:
            labels = load_labels(labels_path)

        if args.refresh or not report_path.exists():
            report = build_comparison_report(
                trace,
                labels,
                window_samples=max(6, int(args.window_samples)),
                persistence_windows=max(1, int(args.baseline_persistence_windows)),
                episode_cooldown_ms=cooldown_ms,
            )
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        else:
            report = json.loads(report_path.read_text(encoding="utf-8"))

        summary_rows.append(
            {
                "telemetry_file": Path(member).name,
                "trace_id": str(report.get("trace_id") or trace_id),
                "label_count": int(report.get("label_count") or 0),
                "incident_classes": sorted({item["incident_class"] for item in labels.get("incidents", [])}),
                "label_granularity": sorted({item.get("label_granularity", "gpu_exact") for item in labels.get("incidents", [])}),
                "lead_time_evaluable_count": int(report["comparison"]["lead_time_evaluable_count"]),
                "sentinel_matched_incident_count": int(report["sentinel"]["matched_incident_count"]),
                "baseline_matched_incident_count": int(report["baseline"]["matched_incident_count"]),
                "matched_incident_delta": int(report["comparison"]["matched_incident_delta"]),
                "sentinel_extra_alert_count": int(report["sentinel"]["extra_alert_count"]),
                "baseline_extra_alert_count": int(report["baseline"]["extra_alert_count"]),
                "extra_alert_delta": int(report["comparison"]["extra_alert_delta"]),
                "sentinel_action_match_count": int(report["sentinel"]["action_match_count"]),
                "baseline_action_match_count": int(report["baseline"]["action_match_count"]),
                "report_path": str(report_path),
                "trace_path": str(trace_path),
                "labels_path": str(labels_path),
            }
        )

    summary_rows.sort(
        key=lambda item: (
            -int(item["matched_incident_delta"]),
            -int(item["extra_alert_delta"]),
            -int(item["sentinel_matched_incident_count"]),
            int(item["sentinel_extra_alert_count"]),
            str(item["telemetry_file"]),
        )
    )

    summary = {
        "generated_at": build_generated_at(),
        "dataset_doi": DEFAULT_GWDG_DOI,
        "dataset_path": str(dataset_path),
        "cache_dir": str(cache_dir),
        "config": {
            "window_samples": max(6, int(args.window_samples)),
            "baseline_persistence_windows": max(1, int(args.baseline_persistence_windows)),
            "episode_cooldown_ms": cooldown_ms,
        },
        "member_count": len(summary_rows),
        "rows": summary_rows,
    }

    output_path = Path(args.output) if args.output else cache_dir / "summary.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"wrote GWDG public sweep summary to {output_path}")
    for row in summary_rows[:5]:
        print(
            f"{row['telemetry_file']}: matched_delta={row['matched_incident_delta']} "
            f"extra_delta={row['extra_alert_delta']} "
            f"sentinel={row['sentinel_matched_incident_count']}/{row['sentinel_extra_alert_count']} "
            f"baseline={row['baseline_matched_incident_count']}/{row['baseline_extra_alert_count']}"
        )
    return 0


def build_generated_at() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
