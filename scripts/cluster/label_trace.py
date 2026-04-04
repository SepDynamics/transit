#!/usr/bin/env python3
"""Create, inspect, and validate incident labels for a replay trace."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cluster.evaluation import (
    load_labels,
    normalize_label_payload,
    summarize_trace_entities,
    validate_label_payload_against_trace,
)
from scripts.cluster.trace_utils import load_trace_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Author and validate exact incident labels from a trace")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="Show trace entities and time range")
    inspect_parser.add_argument("--trace", required=True)
    inspect_parser.add_argument("--json", action="store_true")

    init_parser = subparsers.add_parser("init", help="Initialize an empty labels file for a trace")
    init_parser.add_argument("--trace", required=True)
    init_parser.add_argument("--output", required=True)
    init_parser.add_argument("--dataset-id")
    init_parser.add_argument("--force", action="store_true")

    add_parser = subparsers.add_parser("add", help="Add or replace one incident label")
    add_parser.add_argument("--trace", required=True)
    add_parser.add_argument("--labels", required=True)
    add_parser.add_argument("--incident-id", required=True)
    add_parser.add_argument("--host", required=True)
    add_parser.add_argument("--gpu-index", required=True, type=int)
    add_parser.add_argument("--incident-class", required=True)
    add_parser.add_argument("--onset", required=True, help="Incident onset in epoch ms or ISO-8601 UTC")
    add_parser.add_argument("--end", help="Incident end in epoch ms or ISO-8601 UTC")
    add_parser.add_argument("--duration-minutes", type=int, default=20)
    add_parser.add_argument("--expected-action", required=True)
    add_parser.add_argument("--expected-summary", default="")
    add_parser.add_argument("--onset-precision", default="exact")
    add_parser.add_argument("--label-granularity", default="gpu_exact")

    validate_parser = subparsers.add_parser("validate", help="Validate a labels file against a trace")
    validate_parser.add_argument("--trace", required=True)
    validate_parser.add_argument("--labels", required=True)
    validate_parser.add_argument("--json", action="store_true")

    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "inspect":
        return inspect_trace(args.trace, as_json=bool(args.json))
    if args.command == "init":
        return init_labels(args.trace, args.output, dataset_id=args.dataset_id, force=bool(args.force))
    if args.command == "add":
        return add_incident(args)
    if args.command == "validate":
        return validate_labels(args.trace, args.labels, as_json=bool(args.json))
    raise ValueError(f"unsupported command: {args.command}")


def inspect_trace(trace_path: str, *, as_json: bool) -> int:
    trace = load_trace_file(trace_path)
    summary = summarize_trace_entities(trace)
    if as_json:
        print(json.dumps(summary, indent=2))
        return 0
    print(f"trace_id: {summary['trace_id']}")
    print(f"entity_count: {summary['entity_count']}")
    print(f"start_ms: {summary['start_ms']}")
    print(f"end_ms: {summary['end_ms']}")
    for entity in summary["entities"]:
        print(
            f"{entity['host']} gpu{entity['gpu_index']} "
            f"samples={entity['sample_count']} start_ms={entity['start_ms']} end_ms={entity['end_ms']}"
        )
    return 0


def init_labels(trace_path: str, output_path: str, *, dataset_id: str | None, force: bool) -> int:
    output = Path(output_path)
    if output.exists() and not force:
        raise FileExistsError(f"labels file already exists: {output}")
    trace = load_trace_file(trace_path)
    payload = normalize_label_payload(
        {
            "dataset_id": str(dataset_id or f"{trace.get('trace_id') or output.stem}-labels"),
            "incidents": [],
        },
        default_dataset_id=str(dataset_id or output.stem),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"initialized labels file at {output}")
    return 0


def add_incident(args: argparse.Namespace) -> int:
    trace = load_trace_file(args.trace)
    labels_path = Path(args.labels)
    if labels_path.exists():
        payload = load_labels(labels_path)
    else:
        payload = normalize_label_payload(
            {
                "dataset_id": f"{trace.get('trace_id') or labels_path.stem}-labels",
                "incidents": [],
            },
            default_dataset_id=labels_path.stem,
        )

    onset_ms = parse_timestamp_ms(args.onset)
    end_ms = parse_timestamp_ms(args.end) if args.end else onset_ms + (max(1, int(args.duration_minutes)) * 60 * 1000)
    incident = {
        "incident_id": str(args.incident_id),
        "trace_id": str(trace.get("trace_id") or ""),
        "host": str(args.host),
        "gpu_index": int(args.gpu_index),
        "incident_class": str(args.incident_class),
        "onset_ms": int(onset_ms),
        "end_ms": int(end_ms),
        "onset_precision": str(args.onset_precision),
        "label_granularity": str(args.label_granularity),
        "expected_action": str(args.expected_action),
        "expected_summary": str(args.expected_summary or ""),
    }

    incidents = [item for item in payload["incidents"] if str(item.get("incident_id") or "") != incident["incident_id"]]
    incidents.append(incident)
    updated = normalize_label_payload(
        {
            "dataset_id": payload["dataset_id"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "incidents": incidents,
        },
        default_dataset_id=str(payload["dataset_id"]),
    )
    validation = validate_label_payload_against_trace(updated, trace)
    if not validation["ok"]:
        raise ValueError("; ".join(validation["errors"]))
    labels_path.parent.mkdir(parents=True, exist_ok=True)
    labels_path.write_text(json.dumps(updated, indent=2), encoding="utf-8")
    print(f"wrote labels to {labels_path}")
    for warning in validation["warnings"]:
        print(f"warning: {warning}")
    return 0


def validate_labels(trace_path: str, labels_path: str, *, as_json: bool) -> int:
    validation = validate_label_payload_against_trace(labels_path, trace_path)
    if as_json:
        print(json.dumps(validation, indent=2))
    else:
        print(f"ok: {validation['ok']}")
        print(f"incident_count: {validation['incident_count']}")
        print(f"exact_incident_count: {validation['exact_incident_count']}")
        print(f"coarse_incident_count: {validation['coarse_incident_count']}")
        for warning in validation["warnings"]:
            print(f"warning: {warning}")
        for error in validation["errors"]:
            print(f"error: {error}")
    return 0 if validation["ok"] else 1


def parse_timestamp_ms(value: str | None) -> int:
    if value in (None, ""):
        raise ValueError("timestamp is required")
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    normalized = text.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


if __name__ == "__main__":
    raise SystemExit(main())
