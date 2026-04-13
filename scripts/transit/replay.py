#!/usr/bin/env python3
"""Import archived transit snapshots into the rolling store as a replay trace."""

from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.shared.runtime import isoformat_ms
from scripts.transit.agencies import (
    default_transit_agency_key,
    get_transit_agency_adapter,
)
from scripts.transit.case_packs import (
    resolve_case_pack_event_overlay_path,
    resolve_case_pack_root,
)
from scripts.transit.domain import TransitRuntimeConfig, TransitSnapshotService
from scripts.transit.snapshot_paths import resolve_snapshot_feed_paths
from scripts.transit.store import TransitStore
from scripts.transit.transit_types import TransitReplayTrace

logger = logging.getLogger("transit-replay")


@dataclass
class TransitReplayConfig:
    redis_url: str
    archive_root: Path
    trace_id: str
    snapshot_dirs: List[Path]
    history_retention: int
    system_name: str
    stale_after_seconds: int
    feed_timezone: str
    agency_key: str = default_transit_agency_key()
    clear_trace: bool = False


class TransitReplayService:
    def __init__(
        self, config: TransitReplayConfig, *, store: Optional[TransitStore] = None
    ) -> None:
        self.cfg = config
        self.store = store or TransitStore(config.redis_url)

    def run_once(self) -> Dict[str, Any]:
        snapshot_dirs = self.cfg.snapshot_dirs or discover_snapshot_dirs(
            self.cfg.archive_root
        )
        if not snapshot_dirs:
            raise RuntimeError(
                f"no archived snapshots found under {self.cfg.archive_root}"
            )
        if self.cfg.clear_trace:
            self.store.clear_replay_trace(self.cfg.trace_id)

        imported: List[Dict[str, Any]] = []
        for snapshot_dir in snapshot_dirs:
            manifest = load_snapshot_manifest(snapshot_dir)
            resolved = resolve_snapshot_feed_paths(snapshot_dir)
            snapshot_agency_key = (
                str(manifest.get("agency_key") or self.cfg.agency_key).strip()
                or self.cfg.agency_key
            )
            adapter = get_transit_agency_adapter(snapshot_agency_key)
            case_pack_root = resolve_case_pack_root(snapshot_dir)
            event_overlay_path = (
                resolve_case_pack_event_overlay_path(case_pack_root)
                if case_pack_root
                else None
            )
            runtime = TransitRuntimeConfig(
                system_name=str(
                    manifest.get("agency")
                    or self.cfg.system_name
                    or adapter.system_name
                ),
                agency_key=adapter.key,
                static_feed=_optional_existing_path(resolved.get("static_gtfs")),
                vehicle_positions_feed=_optional_existing_path(
                    resolved.get("vehicle_positions")
                ),
                trip_updates_feed=_optional_existing_path(resolved.get("trip_updates")),
                alerts_feed=_optional_existing_path(resolved.get("alerts")),
                event_overlays_feed=str(event_overlay_path)
                if event_overlay_path
                else None,
                stale_after_seconds=self.cfg.stale_after_seconds,
                feed_timezone=str(
                    manifest.get("feed_timezone")
                    or self.cfg.feed_timezone
                    or adapter.timezone_name
                ),
            )
            snapshot_time_ms = int(
                manifest.get("timestamp_ms")
                or _snapshot_timestamp_from_path(snapshot_dir)
            )
            payload = TransitSnapshotService(runtime).snapshot(now_ms=snapshot_time_ms)
            replay_payload = apply_replay_context(
                payload, trace_id=self.cfg.trace_id, snapshot_time_ms=snapshot_time_ms
            )
            self.store.write_snapshot(
                replay_payload,
                configured_feeds={
                    "static_gtfs": bool(runtime.static_feed),
                    "vehicle_positions": bool(runtime.vehicle_positions_feed),
                    "trip_updates": bool(runtime.trip_updates_feed),
                    "alerts": bool(runtime.alerts_feed),
                },
                retention=self.cfg.history_retention,
                source="replay",
                trace_id=self.cfg.trace_id,
            )
            imported.append(
                {
                    "snapshot_path": str(
                        manifest.get("snapshot_path")
                        or snapshot_dir.relative_to(self.cfg.archive_root)
                    ),
                    "timestamp_ms": snapshot_time_ms,
                    "agency_key": adapter.key,
                    "vehicle_count": len(
                        (replay_payload.get("entities") or {}).get("vehicles") or []
                    ),
                    "incident_count": len(
                        (replay_payload.get("incidents") or {}).get("incidents") or []
                    ),
                }
            )

        status = {
            "status": "ok",
            "trace_id": self.cfg.trace_id,
            "agency_key": self.cfg.agency_key,
            "updated_at": isoformat_ms(),
            "snapshot_count": len(imported),
            "first_snapshot_path": imported[0]["snapshot_path"],
            "latest_snapshot_path": imported[-1]["snapshot_path"],
            "latest_snapshot_timestamp_ms": imported[-1]["timestamp_ms"],
        }
        self.store.write_replay_trace(
            TransitReplayTrace(
                trace_id=self.cfg.trace_id,
                snapshot_count=len(imported),
                first_snapshot_path=str(imported[0]["snapshot_path"]),
                latest_snapshot_path=str(imported[-1]["snapshot_path"]),
                latest_snapshot_timestamp_ms=int(imported[-1]["timestamp_ms"]),
                updated_at=str(status["updated_at"]),
                system_name=self.cfg.system_name,
            )
        )
        self.store.write_status("ops:transit_replay_status", status)
        logger.info(
            "imported %d archived snapshots into transit trace=%s",
            len(imported),
            self.cfg.trace_id,
        )
        return status


def build_parser() -> argparse.ArgumentParser:
    adapter = get_transit_agency_adapter(
        os.getenv("TRANSIT_AGENCY", default_transit_agency_key())
    )
    parser = argparse.ArgumentParser(
        description="Import archived transit snapshots into Valkey as a replay trace"
    )
    parser.add_argument(
        "--redis", default=os.getenv("VALKEY_URL", "redis://localhost:6379/0")
    )
    parser.add_argument("--agency", default=os.getenv("TRANSIT_AGENCY", adapter.key))
    parser.add_argument(
        "--archive-root",
        default=os.getenv("TRANSIT_ARCHIVE_ROOT", adapter.archive_root),
    )
    parser.add_argument("--trace-id", required=True)
    parser.add_argument("--snapshot-dir", action="append", default=[])
    parser.add_argument(
        "--max-snapshots",
        type=int,
        default=0,
        help="Import only the most recent N snapshots when snapshot dirs are auto-discovered",
    )
    parser.add_argument(
        "--history-retention",
        type=int,
        default=int(os.getenv("TRANSIT_HISTORY_RETENTION", "720")),
    )
    parser.add_argument(
        "--system-name", default=os.getenv("TRANSIT_SYSTEM_NAME", adapter.system_name)
    )
    parser.add_argument(
        "--stale-after-seconds",
        type=int,
        default=int(os.getenv("TRANSIT_FEED_STALE_AFTER_SECONDS", "90")),
    )
    parser.add_argument(
        "--feed-timezone",
        default=os.getenv("TRANSIT_FEED_TIMEZONE", adapter.timezone_name),
    )
    parser.add_argument(
        "--clear-trace",
        action="store_true",
        help="Remove any existing replay data for this trace before importing",
    )
    return parser


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    args = build_parser().parse_args()
    adapter = get_transit_agency_adapter(str(args.agency))
    archive_root = Path(args.archive_root).expanduser().resolve()
    snapshot_dirs = [
        Path(value).expanduser().resolve() for value in (args.snapshot_dir or [])
    ]
    if not snapshot_dirs:
        discovered = discover_snapshot_dirs(archive_root)
        if int(args.max_snapshots or 0) > 0:
            discovered = discovered[-int(args.max_snapshots) :]
        snapshot_dirs = discovered
    cfg = TransitReplayConfig(
        redis_url=str(args.redis),
        archive_root=archive_root,
        trace_id=str(args.trace_id),
        snapshot_dirs=snapshot_dirs,
        history_retention=max(12, int(args.history_retention)),
        agency_key=adapter.key,
        system_name=str(args.system_name or adapter.system_name),
        stale_after_seconds=max(30, int(args.stale_after_seconds)),
        feed_timezone=str(args.feed_timezone or adapter.timezone_name),
        clear_trace=bool(args.clear_trace),
    )
    TransitReplayService(cfg).run_once()
    return 0


def discover_snapshot_dirs(archive_root: Path) -> List[Path]:
    archive_root = Path(archive_root)
    snapshot_dirs = sorted(
        path for path in archive_root.glob("archive/*/*/*/*") if path.is_dir()
    )
    snapshot_dirs.sort(key=_snapshot_sort_key)
    return snapshot_dirs


def latest_snapshot_dir(archive_root: Path) -> Optional[Path]:
    snapshot_dirs = discover_snapshot_dirs(archive_root)
    return snapshot_dirs[-1] if snapshot_dirs else None


def select_snapshot_dirs_in_window(
    archive_root: Path,
    *,
    center_timestamp_ms: Optional[int] = None,
    lookback_ms: int = 0,
    lookahead_ms: int = 0,
    max_snapshots: Optional[int] = None,
) -> List[Path]:
    snapshot_dirs = discover_snapshot_dirs(archive_root)
    return filter_snapshot_dirs_in_window(
        snapshot_dirs,
        center_timestamp_ms=center_timestamp_ms,
        lookback_ms=lookback_ms,
        lookahead_ms=lookahead_ms,
        max_snapshots=max_snapshots,
    )


def filter_snapshot_dirs_in_window(
    snapshot_dirs: Sequence[Path],
    *,
    center_timestamp_ms: Optional[int] = None,
    lookback_ms: int = 0,
    lookahead_ms: int = 0,
    max_snapshots: Optional[int] = None,
) -> List[Path]:
    if not snapshot_dirs:
        return []
    normalized_dirs = sorted(
        (Path(path) for path in snapshot_dirs), key=_snapshot_sort_key
    )
    if center_timestamp_ms is None:
        center_timestamp_ms = _snapshot_sort_key(normalized_dirs[-1])[0]
    start_ms = int(center_timestamp_ms) - max(0, int(lookback_ms))
    end_ms = int(center_timestamp_ms) + max(0, int(lookahead_ms))
    selected = [
        path
        for path in normalized_dirs
        if start_ms <= _snapshot_sort_key(path)[0] <= end_ms
    ]
    if not selected:
        nearest = min(
            normalized_dirs,
            key=lambda path: abs(
                _snapshot_sort_key(path)[0] - int(center_timestamp_ms or 0)
            ),
        )
        selected = [nearest]
    if max_snapshots is not None and max_snapshots > 0:
        selected = selected[-int(max_snapshots) :]
    return selected


def load_snapshot_manifest(snapshot_dir: Path) -> Dict[str, Any]:
    manifest_path = Path(snapshot_dir) / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def apply_replay_context(
    payload: Dict[str, Any], *, trace_id: str, snapshot_time_ms: int
) -> Dict[str, Any]:
    replay_payload = copy.deepcopy(payload)
    generated_at = isoformat_ms(snapshot_time_ms)

    def _mark(row: Dict[str, Any]) -> Dict[str, Any]:
        row["source"] = "replay"
        row["trace_id"] = trace_id
        if "timestamp_ms" not in row or not row.get("timestamp_ms"):
            row["timestamp_ms"] = snapshot_time_ms
        return row

    for key in ["health", "entities", "regimes", "incidents"]:
        section = replay_payload.get(key)
        if isinstance(section, dict):
            section["generated_at"] = generated_at

    health = replay_payload.get("health")
    if isinstance(health, dict) and isinstance(health.get("worst_corridor"), dict):
        _mark(health["worst_corridor"])

    entities = replay_payload.get("entities") or {}
    for key in ["lines", "active_lines", "scheduled_later_lines", "inactive_lines"]:
        for row in entities.get(key) or []:
            if isinstance(row, dict):
                _mark(row)
    for vehicle in entities.get("vehicles") or []:
        if not isinstance(vehicle, dict):
            continue
        _mark(vehicle)
        if isinstance(vehicle.get("regime"), dict):
            _mark(vehicle["regime"])
        if isinstance(vehicle.get("observation"), dict):
            _mark(vehicle["observation"])

    regimes = replay_payload.get("regimes") or {}
    for row in regimes.get("regimes") or []:
        if isinstance(row, dict):
            _mark(row)

    incidents = replay_payload.get("incidents") or {}
    for row in incidents.get("incidents") or []:
        if isinstance(row, dict):
            _mark(row)

    return replay_payload


def _optional_existing_path(value: Optional[str]) -> Optional[str]:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    path = Path(candidate)
    return str(path) if path.exists() else None


def _snapshot_sort_key(snapshot_dir: Path) -> tuple[int, str]:
    manifest = load_snapshot_manifest(snapshot_dir)
    timestamp_ms = int(
        manifest.get("timestamp_ms") or _snapshot_timestamp_from_path(snapshot_dir)
    )
    return timestamp_ms, str(snapshot_dir)


def _snapshot_timestamp_from_path(snapshot_dir: Path) -> int:
    try:
        year = int(snapshot_dir.parents[2].name)
        month = int(snapshot_dir.parents[1].name)
        day = int(snapshot_dir.parents[0].name)
        stamp = snapshot_dir.name.rstrip("Z")
        hour = int(stamp[0:2])
        minute = int(stamp[2:4])
        second = int(stamp[4:6])
        from datetime import datetime, timezone

        return int(
            datetime(
                year, month, day, hour, minute, second, tzinfo=timezone.utc
            ).timestamp()
            * 1000
        )
    except (IndexError, ValueError):
        return int(os.path.getmtime(snapshot_dir) * 1000)


if __name__ == "__main__":
    raise SystemExit(main())
