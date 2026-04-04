#!/usr/bin/env python3
"""Generate corridor-level historical summaries from archived transit snapshots."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.transit.domain import TransitRuntimeConfig, TransitSnapshotService
from scripts.transit.snapshot_paths import resolve_snapshot_feed_paths


@dataclass
class SnapshotReport:
    manifest_path: Path
    timestamp_ms: int
    incident_count: int
    line_count: int


def build_archive_report(root_dir: str | Path, *, max_snapshots: Optional[int] = None) -> Dict[str, Any]:
    root = Path(root_dir)
    manifests = sorted(root.glob("archive/*/*/*/*/manifest.json"))
    if max_snapshots is not None:
        manifests = manifests[-max_snapshots:]
    corridor_rows: Dict[str, Dict[str, Any]] = {}
    snapshots: List[SnapshotReport] = []

    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        snapshot_dir = manifest_path.parent
        feed_paths = resolve_snapshot_feed_paths(snapshot_dir)
        snapshot_time_ms = (
            int(manifest.get("timestamp_ms"))
            if manifest.get("timestamp_ms") not in (None, "")
            else None
        )
        service = TransitSnapshotService(
            TransitRuntimeConfig(
                system_name=str(manifest.get("agency") or "Transit Sentinel"),
                static_feed=feed_paths["static_gtfs"],
                vehicle_positions_feed=feed_paths["vehicle_positions"],
                trip_updates_feed=feed_paths["trip_updates"],
                alerts_feed=feed_paths["alerts"],
                stale_after_seconds=max(30, int(os.getenv("TRANSIT_FEED_STALE_AFTER_SECONDS", "90"))),
                feed_timezone=os.getenv(
                    "TRANSIT_FEED_TIMEZONE",
                    "America/New_York" if str(manifest.get("agency") or "").strip().upper() == "MBTA" else "UTC",
                ),
            )
        )
        regimes = service.regimes(now_ms=snapshot_time_ms)["regimes"]
        incidents = service.incidents(now_ms=snapshot_time_ms)["incidents"]
        snapshots.append(
            SnapshotReport(
                manifest_path=manifest_path,
                timestamp_ms=int(manifest.get("timestamp_ms") or 0),
                incident_count=len(incidents),
                line_count=len(regimes),
            )
        )
        incident_ids = {row["entity_id"] for row in incidents}
        for regime in regimes:
            entity_id = str(regime["entity_id"])
            row = corridor_rows.setdefault(
                entity_id,
                {
                    "entity_id": entity_id,
                    "label": regime.get("label"),
                    "route_id": regime.get("route_id"),
                    "snapshot_count": 0,
                    "incident_snapshot_count": 0,
                    "hazard_sum": 0.0,
                    "hazard_max": 0.0,
                    "action_counts": Counter(),
                    "regime_counts": Counter(),
                },
            )
            row["snapshot_count"] += 1
            row["hazard_sum"] += float(regime.get("hazard") or 0.0)
            row["hazard_max"] = max(row["hazard_max"], float(regime.get("hazard") or 0.0))
            row["action_counts"][str(regime.get("action") or "monitor")] += 1
            row["regime_counts"][str(regime.get("regime") or "healthy")] += 1
            if entity_id in incident_ids:
                row["incident_snapshot_count"] += 1

    corridors = []
    for row in corridor_rows.values():
        snapshot_count = max(1, int(row["snapshot_count"]))
        avg_hazard = row["hazard_sum"] / snapshot_count
        corridors.append(
            {
                "entity_id": row["entity_id"],
                "label": row["label"],
                "route_id": row["route_id"],
                "snapshot_count": snapshot_count,
                "incident_snapshot_count": int(row["incident_snapshot_count"]),
                "incident_rate": round(float(row["incident_snapshot_count"]) / snapshot_count, 4),
                "avg_hazard": round(avg_hazard, 4),
                "hazard_max": round(float(row["hazard_max"]), 4),
                "top_action": row["action_counts"].most_common(1)[0][0] if row["action_counts"] else "monitor",
                "top_regime": row["regime_counts"].most_common(1)[0][0] if row["regime_counts"] else "healthy",
                "action_counts": dict(sorted(row["action_counts"].items())),
                "regime_counts": dict(sorted(row["regime_counts"].items())),
            }
        )
    corridors.sort(key=lambda item: (-float(item["incident_rate"]), -float(item["avg_hazard"]), str(item["label"] or "")))

    return {
        "snapshot_count": len(snapshots),
        "time_range": {
            "first_timestamp_ms": min((row.timestamp_ms for row in snapshots), default=None),
            "last_timestamp_ms": max((row.timestamp_ms for row in snapshots), default=None),
        },
        "snapshots": [
            {
                "manifest_path": str(row.manifest_path),
                "timestamp_ms": row.timestamp_ms,
                "incident_count": row.incident_count,
                "line_count": row.line_count,
            }
            for row in snapshots
        ],
        "corridors": corridors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate corridor history report from archived transit snapshots")
    parser.add_argument("--root-dir", default=os.getenv("TRANSIT_ARCHIVE_ROOT", "data/feeds/mbta"))
    parser.add_argument("--max-snapshots", type=int, default=None)
    parser.add_argument("--output", default="-")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = build_archive_report(args.root_dir, max_snapshots=args.max_snapshots)
    body = json.dumps(payload, indent=2, sort_keys=True)
    if args.output == "-":
        print(body)
    else:
        Path(args.output).write_text(body, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
