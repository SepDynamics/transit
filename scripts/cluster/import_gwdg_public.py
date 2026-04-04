#!/usr/bin/env python3
"""Download and convert the public GWDG Zenodo dataset into Sentinel trace artifacts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cluster.gwdg_dataset import (
    DEFAULT_GWDG_ARCHIVE_NAME,
    build_label_payload_from_gwdg_archive,
    build_trace_from_gwdg_archive,
    download_gwdg_dataset,
    list_gwdg_telemetry_members,
)
from scripts.cluster.trace_utils import anonymize_trace_payload, write_trace_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import the public GWDG GPU telemetry dataset into Sentinel trace format")
    parser.add_argument(
        "--dataset",
        default=str(Path("output") / "public" / DEFAULT_GWDG_ARCHIVE_NAME),
        help="Path to the local GWDG Zenodo zip archive",
    )
    parser.add_argument("--download", action="store_true", help="Download the archive from Zenodo if it is missing")
    parser.add_argument("--list", action="store_true", help="List telemetry members available in the archive and exit")
    parser.add_argument("--include-baselines", action="store_true", help="Include non-incident baseline windows in --list output")
    parser.add_argument("--telemetry-file", help="Telemetry member selector, such as ggpu121_2025-02-10_gpu-error")
    parser.add_argument("--output-trace", help="Trace JSON output path")
    parser.add_argument("--output-labels", help="Optional incident labels JSON output path")
    parser.add_argument("--trace-id", help="Override trace identifier")
    parser.add_argument("--anonymize", action="store_true", help="Apply an extra anonymization pass to the already sanitized trace")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        if not args.download:
            raise FileNotFoundError(f"dataset archive not found: {dataset_path}")
        download_gwdg_dataset(dataset_path)
        print(f"downloaded public GWDG dataset to {dataset_path}")

    if args.list:
        for member in list_gwdg_telemetry_members(dataset_path, include_baselines=bool(args.include_baselines)):
            print(Path(member).name)
        return 0

    if not args.telemetry_file:
        raise ValueError("--telemetry-file is required unless --list is used")
    if not args.output_trace:
        raise ValueError("--output-trace is required when importing a telemetry member")

    raw_trace = build_trace_from_gwdg_archive(
        dataset_path,
        telemetry_selector=str(args.telemetry_file),
        trace_id=args.trace_id,
        anonymize=False,
    )
    trace = anonymize_trace_payload(raw_trace) if args.anonymize else raw_trace
    write_trace_file(args.output_trace, trace)
    print(f"wrote public trace {trace['trace_id']} to {args.output_trace}")

    if args.output_labels:
        labels = build_label_payload_from_gwdg_archive(
            dataset_path,
            telemetry_selector=str(args.telemetry_file),
            trace=raw_trace,
        )
        if args.anonymize:
            labels = remap_label_hosts(labels, source_trace=raw_trace, target_trace=trace)
        output_path = Path(args.output_labels)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(labels, indent=2), encoding="utf-8")
        print(f"wrote {len(labels['incidents'])} public labels to {output_path}")
    return 0


def remap_label_hosts(labels: dict[str, object], *, source_trace: dict[str, object], target_trace: dict[str, object]) -> dict[str, object]:
    host_map: dict[str, str] = {}
    for source_entity, target_entity in zip(source_trace.get("entities", []), target_trace.get("entities", [])):
        if not isinstance(source_entity, dict) or not isinstance(target_entity, dict):
            continue
        host_map[str(source_entity.get("host") or "")] = str(target_entity.get("host") or "")
    remapped = json.loads(json.dumps(labels))
    incidents = remapped.get("incidents")
    if isinstance(incidents, list):
        for incident in incidents:
            if not isinstance(incident, dict):
                continue
            host = str(incident.get("host") or "")
            if host in host_map:
                incident["host"] = host_map[host]
    return remapped


if __name__ == "__main__":
    raise SystemExit(main())
