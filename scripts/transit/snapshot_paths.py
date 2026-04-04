"""Helpers for resolving archived transit snapshot feed paths."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict


SNAPSHOT_FEED_FILENAMES = {
    "static_gtfs": "MBTA_GTFS.zip",
    "vehicle_positions": "VehiclePositions_enhanced.json",
    "trip_updates": "TripUpdates_enhanced.json",
    "alerts": "Alerts_enhanced.json",
}


def resolve_snapshot_feed_paths(snapshot_dir: Path) -> Dict[str, str]:
    snapshot_dir = Path(snapshot_dir)
    resolved = {name: str(snapshot_dir / filename) for name, filename in SNAPSHOT_FEED_FILENAMES.items()}
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.exists():
        return resolved
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return resolved

    feeds = manifest.get("feeds")
    if not isinstance(feeds, list):
        return resolved

    root_dir = _archive_root_from_manifest(snapshot_dir, manifest)
    for row in feeds:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        relative_path = str(row.get("path") or "").strip()
        if name not in resolved or not relative_path:
            continue
        candidate = Path(relative_path)
        if not candidate.is_absolute():
            candidate = root_dir / candidate
        resolved[name] = str(candidate.resolve())
    return resolved


def _archive_root_from_manifest(snapshot_dir: Path, manifest: Dict[str, object]) -> Path:
    snapshot_path = str(manifest.get("snapshot_path") or "").strip()
    if not snapshot_path:
        return snapshot_dir.parent
    root_dir = snapshot_dir
    for _ in Path(snapshot_path).parts:
        root_dir = root_dir.parent
    return root_dir
