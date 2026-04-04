#!/usr/bin/env python3
"""Snapshot recent telemetry into a replayable trace file."""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Dict, List

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cluster.storage import ClusterStore
from scripts.cluster.trace_utils import anonymize_trace_payload, write_trace_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record recent telemetry history into a trace JSON file")
    parser.add_argument("--redis", default=os.getenv("VALKEY_URL", "redis://localhost:6379/0"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=180)
    parser.add_argument("--scope", default="live", choices=["all", "live", "replay"])
    parser.add_argument("--anonymize", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    store = ClusterStore(args.redis)
    entities = store.list_entities(scope=args.scope)
    payload: Dict[str, Any] = {
        "trace_id": Path(args.output).stem,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "scope": args.scope,
        "entities": [],
    }
    for entity in entities:
        host = str(entity["host"])
        gpu_index = int(entity["gpu_index"])
        samples = store.get_recent_samples(host, gpu_index, limit=max(10, int(args.limit)))
        if not samples:
            continue
        payload["entities"].append(
            {
                "host": host,
                "gpu_index": gpu_index,
                "sample": entity.get("sample") or {},
                "samples": samples,
            }
        )
    if args.anonymize:
        payload = anonymize_trace_payload(payload)
    output_path = Path(args.output)
    write_trace_file(output_path, payload)
    print(f"wrote trace with {len(payload['entities'])} entities to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
