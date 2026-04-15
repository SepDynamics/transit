"""Helpers for resolving archived transit snapshot feed paths."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from scripts.transit.agencies import default_transit_agency_key, get_transit_agency_adapter


def snapshot_feed_filenames(agency_key: str | None = None) -> Dict[str, str]:
    adapter = get_transit_agency_adapter(agency_key or default_transit_agency_key())
    return {
        "static_gtfs": adapter.static_feed_filename,
        "vehicle_positions": adapter.vehicle_positions_filename,
        "trip_updates": adapter.trip_updates_filename,
        "alerts": adapter.alerts_filename,
    }


def resolve_snapshot_feed_paths(snapshot_dir: Path) -> Dict[str, str]:
    snapshot_dir = Path(snapshot_dir)
    manifest_path = snapshot_dir / "manifest.json"
    manifest: Dict[str, object] = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
    agency_key = _snapshot_agency_key(manifest)
    resolved = {name: str(snapshot_dir / filename) for name, filename in snapshot_feed_filenames(agency_key).items()}
    if not manifest_path.exists():
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


def _snapshot_agency_key(manifest: Dict[str, object]) -> str:
    explicit = str(manifest.get("agency_key") or "").strip().lower()
    if explicit:
        return explicit
    agency = str(manifest.get("agency") or "").strip().lower()
    if agency == "mbta":
        return "mbta"
    return default_transit_agency_key()
