#!/usr/bin/env python3
"""Persist archive-backed proof windows around detected transit incidents."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.shared.runtime import isoformat_ms
from scripts.transit.agencies import default_transit_agency_key
from scripts.transit.replay import (
    _snapshot_timestamp_from_path,
    filter_snapshot_dirs_in_window,
    load_snapshot_manifest,
)


@dataclass
class TransitProofWindowConfig:
    archive_root: Path
    output_root: Path
    incident: Dict[str, Any]
    before_minutes: int = 60
    after_minutes: int = 60
    agency_key: Optional[str] = None


def capture_proof_window(config: TransitProofWindowConfig) -> Dict[str, Any]:
    archive_root = Path(config.archive_root).expanduser().resolve()
    output_root = Path(config.output_root).expanduser().resolve()
    incident = dict(config.incident or {})
    timestamp_ms = int(incident.get("timestamp_ms") or time.time() * 1000)
    snapshot_dirs = filter_snapshot_dirs_in_window(
        discover_archived_snapshots(archive_root),
        center_timestamp_ms=timestamp_ms,
        lookback_ms=max(1, int(config.before_minutes)) * 60 * 1000,
        lookahead_ms=max(1, int(config.after_minutes)) * 60 * 1000,
    )
    if not snapshot_dirs:
        raise RuntimeError(f"no archived snapshots found under {archive_root}")

    agency_key = (
        str(config.agency_key or incident.get("agency_key") or "").strip().lower()
        or default_transit_agency_key()
    )
    incident_id = str(incident.get("incident_id") or incident.get("entity_id") or "incident")
    bundle_id = build_proof_window_id(incident_id, timestamp_ms)
    bundle_root = output_root / agency_key / bundle_id
    bundle_root.mkdir(parents=True, exist_ok=True)

    copied_snapshots = []
    for snapshot_dir in snapshot_dirs:
        relative_snapshot_path = snapshot_path_label(snapshot_dir, archive_root)
        destination = bundle_root / relative_snapshot_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(snapshot_dir, destination, dirs_exist_ok=True)
        manifest = load_snapshot_manifest(snapshot_dir)
        copied_snapshots.append(
            {
                "snapshot_path": relative_snapshot_path,
                "timestamp_ms": int(
                    manifest.get("timestamp_ms") or _snapshot_timestamp_from_path(snapshot_dir)
                ),
            }
        )

    proof_window = {
        "status": "ok",
        "captured_at": isoformat_ms(),
        "bundle_id": bundle_id,
        "agency_key": agency_key,
        "archive_root": str(archive_root),
        "bundle_root": str(bundle_root),
        "incident": incident,
        "window": {
            "before_minutes": max(1, int(config.before_minutes)),
            "after_minutes": max(1, int(config.after_minutes)),
            "center_timestamp_ms": timestamp_ms,
            "center_timestamp": isoformat_ms(timestamp_ms),
        },
        "snapshot_count": len(copied_snapshots),
        "snapshots": copied_snapshots,
    }
    write_json(bundle_root / "proof_window.json", proof_window)
    write_json(bundle_root / "incident.json", incident)
    return proof_window


def discover_archived_snapshots(archive_root: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in Path(archive_root).glob("archive/*/*/*/*")
            if path.is_dir()
        ),
        key=lambda path: (
            int(load_snapshot_manifest(path).get("timestamp_ms") or _snapshot_timestamp_from_path(path)),
            str(path),
        ),
    )


def snapshot_path_label(snapshot_dir: Path, archive_root: Path) -> str:
    manifest = load_snapshot_manifest(snapshot_dir)
    explicit = str(manifest.get("snapshot_path") or "").strip()
    if explicit:
        return explicit
    try:
        return str(Path(snapshot_dir).resolve().relative_to(Path(archive_root).resolve()))
    except ValueError:
        return str(Path(snapshot_dir).resolve())


def build_proof_window_id(incident_id: str, timestamp_ms: int) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(timestamp_ms / 1000.0))
    return f"{stamp}-{slugify(incident_id) or 'incident'}"


def slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    return text.strip("-")


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Persist an archive-backed proof window around a transit incident"
    )
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--output-root", default="artifacts/proof-windows")
    parser.add_argument("--incident-json", required=True)
    parser.add_argument("--before-minutes", type=int, default=60)
    parser.add_argument("--after-minutes", type=int, default=60)
    parser.add_argument("--agency-key", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    incident = json.loads(Path(args.incident_json).read_text(encoding="utf-8"))
    proof_window = capture_proof_window(
        TransitProofWindowConfig(
            archive_root=Path(args.archive_root),
            output_root=Path(args.output_root),
            incident=incident,
            before_minutes=max(1, int(args.before_minutes)),
            after_minutes=max(1, int(args.after_minutes)),
            agency_key=str(args.agency_key or "").strip() or None,
        )
    )
    print(json.dumps(proof_window, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
