#!/usr/bin/env python3
"""Import recorded Prometheus/DCGM snapshots into a validated replay trace."""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Sequence, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cluster.trace_utils import build_trace_from_prometheus_snapshots, write_trace_file

TIMESTAMP_RE = re.compile(r"(?P<stamp>\d{10,14})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import recorded Prometheus/DCGM snapshots into a trace JSON file")
    parser.add_argument("--input-dir", help="Directory containing Prometheus snapshot files")
    parser.add_argument("--snapshot", action="append", default=[], help="Snapshot file path. Can be provided multiple times.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--trace-id", help="Trace identifier. Defaults to the output file stem.")
    parser.add_argument("--host", help="Override hostname when the snapshots do not include one.")
    parser.add_argument("--interval-seconds", type=float, default=float(os.getenv("TRACE_IMPORT_INTERVAL_SECONDS", "5.0")))
    parser.add_argument("--start-ms", type=int, help="Base timestamp in milliseconds when filenames do not encode time.")
    parser.add_argument("--collection-source", default="dcgm_exporter")
    parser.add_argument("--anonymize", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    snapshot_paths = discover_snapshot_paths(input_dir=args.input_dir, explicit=args.snapshot)
    snapshots: List[Tuple[int, str]] = []
    for index, path in enumerate(snapshot_paths):
        timestamp_ms = infer_timestamp_ms(
            path,
            index=index,
            start_ms=args.start_ms,
            interval_seconds=max(0.1, float(args.interval_seconds)),
        )
        snapshots.append((timestamp_ms, path.read_text(encoding="utf-8")))
    trace_id = str(args.trace_id or Path(args.output).stem)
    payload = build_trace_from_prometheus_snapshots(
        snapshots,
        trace_id=trace_id,
        host=args.host,
        collection_source=str(args.collection_source or "dcgm_exporter"),
        anonymize=bool(args.anonymize),
        metadata={"imported_from": [str(path) for path in snapshot_paths]},
    )
    write_trace_file(args.output, payload)
    print(f"wrote trace {trace_id} with {len(payload['entities'])} entities from {len(snapshot_paths)} snapshots to {args.output}")
    return 0


def discover_snapshot_paths(*, input_dir: str | None, explicit: Sequence[str]) -> List[Path]:
    paths = [Path(item) for item in explicit if item]
    if input_dir:
        directory = Path(input_dir)
        if not directory.is_dir():
            raise FileNotFoundError(f"snapshot directory not found: {directory}")
        paths.extend(sorted(path for path in directory.iterdir() if path.is_file()))
    deduped = sorted({path.resolve() for path in paths})
    if not deduped:
        raise FileNotFoundError("no snapshot files found")
    return deduped


def infer_timestamp_ms(path: Path, *, index: int, start_ms: int | None, interval_seconds: float) -> int:
    match = TIMESTAMP_RE.search(path.stem)
    if match:
        stamp = match.group("stamp")
        if len(stamp) == 13:
            return int(stamp)
        if len(stamp) == 10:
            return int(stamp) * 1000
        if len(stamp) == 14:
            dt = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
    base_ms = int(start_ms if start_ms is not None else int(time.time() * 1000))
    return base_ms + int(index * interval_seconds * 1000)


if __name__ == "__main__":
    raise SystemExit(main())
