#!/usr/bin/env python3
"""Seed a repeatable demo state into Valkey from archive windows and demo fixtures."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.shared.runtime import isoformat_ms
from scripts.transit.agencies import default_transit_agency_key, get_transit_agency_adapter
from scripts.transit.case_packs import (
    CASE_PACK_METADATA_FILENAME,
    load_case_pack_metadata,
    resolve_case_pack_event_overlay_path,
)
from scripts.transit.domain import TransitRuntimeConfig, TransitSnapshotService
from scripts.transit.replay import (
    TransitReplayConfig,
    TransitReplayService,
    _snapshot_timestamp_from_path,
    discover_snapshot_dirs,
    filter_snapshot_dirs_in_window,
    load_snapshot_manifest,
)
from scripts.transit.snapshot_paths import resolve_snapshot_feed_paths
from scripts.transit.store import TransitStore

logger = logging.getLogger("transit-demo-seed")


@dataclass
class TransitDemoSeedConfig:
    redis_url: str
    live_case_pack_root: Optional[Path]
    live_snapshot_dir: Optional[Path]
    live_archive_root: Optional[Path]
    replay_case_pack_roots: List[Path]
    replay_archive_roots: List[Path]
    replay_window_minutes: int
    replay_max_snapshots: int
    history_retention: int
    stale_after_seconds: int
    clear_store: bool = False
    trace_prefix: str = ""
    output_path: Optional[Path] = None


class TransitDemoSeedService:
    def __init__(self, config: TransitDemoSeedConfig, *, store: Optional[TransitStore] = None) -> None:
        self.cfg = config
        self.store = store or TransitStore(config.redis_url)

    def run_once(self) -> Dict[str, Any]:
        cleared_key_count = 0
        if self.cfg.clear_store:
            cleared_key_count = self.store.clear_runtime_state()

        live_seed = self._seed_live_snapshot() if self.cfg.live_case_pack_root else None
        if live_seed is None and (
            self.cfg.live_snapshot_dir or self.cfg.live_archive_root
        ):
            live_seed = self._seed_live_snapshot()
        replay_traces: List[Dict[str, Any]] = []
        seen_trace_ids: set[str] = set()
        for archive_root in self._ordered_archive_roots():
            trace = self._seed_replay_archive_trace(archive_root)
            trace_id = str(trace.get("trace_id") or "")
            if trace_id in seen_trace_ids:
                raise ValueError(f"duplicate demo trace_id generated for {archive_root}: {trace_id}")
            seen_trace_ids.add(trace_id)
            replay_traces.append(trace)
        for case_pack_root in self._ordered_case_pack_roots():
            trace = self._seed_replay_case_pack_trace(case_pack_root)
            trace_id = str(trace.get("trace_id") or "")
            if trace_id in seen_trace_ids:
                raise ValueError(f"duplicate demo trace_id generated for {case_pack_root}: {trace_id}")
            seen_trace_ids.add(trace_id)
            replay_traces.append(trace)

        status = {
            "status": "ok",
            "seeded_at": isoformat_ms(),
            "cleared_key_count": cleared_key_count,
            "history_retention": self.cfg.history_retention,
            "live_seeded": bool(live_seed),
            "live_seed": live_seed,
            "replay_trace_count": len(replay_traces),
            "replay_traces": replay_traces,
        }
        self.store.write_status("ops:transit_demo_seed_status", status)
        logger.info(
            "seeded transit demo state with live=%s replay_traces=%d",
            "yes" if live_seed else "no",
            len(replay_traces),
        )
        if self.cfg.output_path:
            self._write_output(self.cfg.output_path, status)
        return status

    def _seed_live_snapshot(self) -> Dict[str, Any]:
        case_pack_root = (
            Path(self.cfg.live_case_pack_root).resolve()
            if self.cfg.live_case_pack_root
            else None
        )
        archive_root = (
            Path(self.cfg.live_archive_root).resolve()
            if self.cfg.live_archive_root
            else None
        )
        snapshot_dir = (
            Path(self.cfg.live_snapshot_dir).resolve()
            if self.cfg.live_snapshot_dir
            else latest_snapshot_dir(case_pack_root or archive_root or Path())
        )
        inferred_case_pack_root = case_pack_root_for_snapshot(snapshot_dir)
        if case_pack_root is None:
            case_pack_root = inferred_case_pack_root
        case_pack_metadata = (
            load_case_pack_metadata(case_pack_root) if case_pack_root else {}
        )
        manifest = load_snapshot_manifest(snapshot_dir)
        source_root = case_pack_root or archive_root or snapshot_dir.parent
        runtime = build_snapshot_runtime_config(
            snapshot_dir,
            system_name=str(
                case_pack_metadata.get("event_name")
                or case_pack_metadata.get("case_pack_id")
                or manifest.get("agency")
                or "Transit Demo"
            ),
            stale_after_seconds=self.cfg.stale_after_seconds,
        )
        snapshot_time_ms = int(manifest.get("timestamp_ms") or _snapshot_timestamp_from_path(snapshot_dir))
        payload = TransitSnapshotService(runtime).snapshot(now_ms=snapshot_time_ms)
        configured_feeds = {
            "static_gtfs": bool(runtime.static_feed),
            "vehicle_positions": bool(runtime.vehicle_positions_feed),
            "trip_updates": bool(runtime.trip_updates_feed),
            "alerts": bool(runtime.alerts_feed),
            "event_overlays": bool(runtime.event_overlays_feed),
        }
        self.store.write_snapshot(
            payload,
            configured_feeds=configured_feeds,
            retention=self.cfg.history_retention,
            source="live",
        )
        archive_manifest = dict(manifest)
        archive_manifest["snapshot_path"] = snapshot_path_label(snapshot_dir, root=source_root)
        archive_manifest["case_pack_id"] = case_pack_metadata.get("case_pack_id") if case_pack_root else None
        archive_manifest["seed_mode"] = "demo_live"
        archive_manifest["seed_source"] = (
            "case_pack" if case_pack_root else "archive_snapshot"
        )
        ingest_status = {
            "system_name": runtime.system_name,
            "agency_key": runtime.agency_key,
            "status": "ok" if not (payload.get("errors") or []) else "degraded",
            "updated_at": isoformat_ms(),
            "feed_status": payload.get("feed_status") or {},
            "errors": list(payload.get("errors") or []),
            "archive_manifest": archive_manifest,
        }
        self.store.write_status("ops:transit_ingest_status", ingest_status)
        entities = payload.get("entities") or {}
        incidents = payload.get("incidents") or {}
        return {
            "case_pack_id": case_pack_metadata.get("case_pack_id"),
            "case_pack_root": str(case_pack_root) if case_pack_root else None,
            "archive_root": str(archive_root) if archive_root else None,
            "snapshot_dir": str(snapshot_dir),
            "snapshot_path": archive_manifest["snapshot_path"],
            "timestamp_ms": snapshot_time_ms,
            "agency_key": runtime.agency_key,
            "system_name": runtime.system_name,
            "source_type": "case_pack" if case_pack_root else "archive_snapshot",
            "route_count": len((entities.get("active_lines") or [])) + len((entities.get("scheduled_later_lines") or [])),
            "vehicle_count": len((entities.get("vehicles") or [])),
            "incident_count": len((incidents.get("incidents") or [])),
        }

    def _seed_replay_case_pack_trace(self, case_pack_root: Path) -> Dict[str, Any]:
        metadata = load_case_pack_metadata(case_pack_root)
        agency_key = first_agency_key(metadata)
        adapter = get_transit_agency_adapter(agency_key)
        trace_id = trace_id_for_case_pack(
            str(metadata.get("case_pack_id") or case_pack_root.name),
            prefix=self.cfg.trace_prefix,
        )
        status = TransitReplayService(
            TransitReplayConfig(
                redis_url=self.cfg.redis_url,
                archive_root=case_pack_root,
                trace_id=trace_id,
                snapshot_dirs=[],
                history_retention=self.cfg.history_retention,
                system_name=str(metadata.get("event_name") or metadata.get("case_pack_id") or case_pack_root.name),
                stale_after_seconds=self.cfg.stale_after_seconds,
                feed_timezone=adapter.timezone_name,
                agency_key=adapter.key,
                clear_trace=True,
            ),
            store=self.store,
        ).run_once()
        return {
            "trace_id": trace_id,
            "case_pack_id": metadata.get("case_pack_id"),
            "case_pack_root": str(case_pack_root),
            "city_key": metadata.get("city_key"),
            "event_key": metadata.get("event_key"),
            "agency_keys": list(metadata.get("agency_keys") or []),
            "snapshot_count": status.get("snapshot_count"),
            "latest_snapshot_timestamp_ms": status.get("latest_snapshot_timestamp_ms"),
            "latest_snapshot_path": status.get("latest_snapshot_path"),
            "source_type": "case_pack",
        }

    def _seed_replay_archive_trace(self, archive_root: Path) -> Dict[str, Any]:
        latest_dir = latest_snapshot_dir(archive_root)
        if latest_dir is None:
            raise RuntimeError(f"no archived snapshots found under {archive_root}")
        latest_manifest = load_snapshot_manifest(latest_dir)
        latest_timestamp_ms = int(
            latest_manifest.get("timestamp_ms") or _snapshot_timestamp_from_path(latest_dir)
        )
        snapshot_dirs = filter_snapshot_dirs_in_window(
            discover_snapshot_dirs(archive_root),
            center_timestamp_ms=latest_timestamp_ms,
            lookback_ms=max(1, int(self.cfg.replay_window_minutes)) * 60 * 1000,
            lookahead_ms=0,
            max_snapshots=self.cfg.replay_max_snapshots,
        )
        agency_key = archive_agency_key(archive_root, latest_manifest)
        adapter = get_transit_agency_adapter(agency_key)
        trace_id = trace_id_for_archive_root(
            archive_root,
            agency_key=adapter.key,
            prefix=self.cfg.trace_prefix,
        )
        status = TransitReplayService(
            TransitReplayConfig(
                redis_url=self.cfg.redis_url,
                archive_root=archive_root,
                trace_id=trace_id,
                snapshot_dirs=snapshot_dirs,
                history_retention=self.cfg.history_retention,
                system_name=str(latest_manifest.get("agency") or adapter.system_name),
                stale_after_seconds=self.cfg.stale_after_seconds,
                feed_timezone=str(
                    latest_manifest.get("feed_timezone") or adapter.timezone_name
                ),
                agency_key=adapter.key,
                clear_trace=True,
            ),
            store=self.store,
        ).run_once()
        return {
            "trace_id": trace_id,
            "archive_root": str(archive_root),
            "agency_key": adapter.key,
            "system_name": str(latest_manifest.get("agency") or adapter.system_name),
            "snapshot_count": status.get("snapshot_count"),
            "latest_snapshot_timestamp_ms": status.get("latest_snapshot_timestamp_ms"),
            "latest_snapshot_path": status.get("latest_snapshot_path"),
            "window_minutes": max(1, int(self.cfg.replay_window_minutes)),
            "source_type": "archive_window",
        }

    def _ordered_case_pack_roots(self) -> List[Path]:
        unique_roots = {Path(path).resolve() for path in self.cfg.replay_case_pack_roots}
        return sorted(unique_roots, key=lambda path: str(path))

    def _ordered_archive_roots(self) -> List[Path]:
        unique_roots = {Path(path).resolve() for path in self.cfg.replay_archive_roots}
        return sorted(unique_roots, key=lambda path: str(path))

    @staticmethod
    def _write_output(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed a repeatable demo state into Valkey from archive windows and demo fixtures")
    parser.add_argument("--redis", default=os.getenv("VALKEY_URL", "redis://localhost:6379/0"))
    parser.add_argument(
        "--live-case-pack-root",
        default=os.getenv("TRANSIT_DEMO_LIVE_CASE_PACK", ""),
    )
    parser.add_argument(
        "--live-archive-root",
        default=os.getenv("TRANSIT_DEMO_LIVE_ARCHIVE_ROOT", ""),
        help="Archive root used to seed the live scope from its latest snapshot",
    )
    parser.add_argument("--live-snapshot-dir", default=os.getenv("TRANSIT_DEMO_LIVE_SNAPSHOT_DIR", ""))
    parser.add_argument("--skip-live", action="store_true", help="Skip seeding the live scope and load replay traces only")
    parser.add_argument("--replay-case-pack-root", action="append", default=[])
    parser.add_argument("--replay-archive-root", action="append", default=[])
    parser.add_argument(
        "--replay-case-pack-catalog",
        default=os.getenv("TRANSIT_DEMO_CASE_PACK_ROOT", "data/case-packs"),
        help="Root used to auto-discover replay case packs when --replay-case-pack-root is omitted",
    )
    parser.add_argument(
        "--replay-window-minutes",
        type=int,
        default=int(os.getenv("TRANSIT_DEMO_REPLAY_WINDOW_MINUTES", "120")),
        help="Trailing replay window, anchored to the latest snapshot in each archive root",
    )
    parser.add_argument(
        "--replay-max-snapshots",
        type=int,
        default=int(os.getenv("TRANSIT_DEMO_REPLAY_MAX_SNAPSHOTS", "240")),
        help="Cap the number of snapshots imported for each archive-backed replay trace",
    )
    parser.add_argument("--trace-prefix", default=os.getenv("TRANSIT_DEMO_TRACE_PREFIX", "demo"))
    parser.add_argument("--history-retention", type=int, default=int(os.getenv("TRANSIT_HISTORY_RETENTION", "720")))
    parser.add_argument("--stale-after-seconds", type=int, default=int(os.getenv("TRANSIT_FEED_STALE_AFTER_SECONDS", "90")))
    parser.add_argument("--clear-store", action="store_true", help="Remove existing transit runtime keys before seeding")
    parser.add_argument("--output")
    return parser


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
    args = build_parser().parse_args()
    live_case_pack_root = optional_path(args.live_case_pack_root)
    live_archive_root = optional_path(args.live_archive_root)
    live_snapshot_dir = optional_path(args.live_snapshot_dir)
    if not args.skip_live and not live_case_pack_root and not live_archive_root and not live_snapshot_dir:
        defaults = default_live_seed_sources()
        live_case_pack_root = defaults.get("case_pack_root")
        live_archive_root = defaults.get("archive_root")
        live_snapshot_dir = defaults.get("snapshot_dir")

    replay_archive_roots = [Path(value).expanduser().resolve() for value in (args.replay_archive_root or [])]
    if not replay_archive_roots:
        replay_archive_roots = discover_demo_archive_roots()
    replay_case_pack_roots = [Path(value).expanduser().resolve() for value in (args.replay_case_pack_root or [])]
    if not replay_archive_roots and not replay_case_pack_roots:
        replay_case_pack_roots = discover_case_pack_roots(Path(args.replay_case_pack_catalog).expanduser().resolve())
    cfg = TransitDemoSeedConfig(
        redis_url=str(args.redis),
        live_case_pack_root=None if args.skip_live else live_case_pack_root,
        live_snapshot_dir=None if args.skip_live else live_snapshot_dir,
        live_archive_root=None if args.skip_live else live_archive_root,
        replay_case_pack_roots=replay_case_pack_roots,
        replay_archive_roots=replay_archive_roots,
        replay_window_minutes=max(5, int(args.replay_window_minutes)),
        replay_max_snapshots=max(1, int(args.replay_max_snapshots)),
        history_retention=max(12, int(args.history_retention)),
        stale_after_seconds=max(30, int(args.stale_after_seconds)),
        clear_store=bool(args.clear_store),
        trace_prefix=str(args.trace_prefix or "").strip(),
        output_path=optional_path(args.output),
    )
    status = TransitDemoSeedService(cfg).run_once()
    body = json.dumps(status, indent=2, sort_keys=True)
    if cfg.output_path:
        print(f"wrote transit demo seed manifest to {cfg.output_path}")
    else:
        print(body)
    return 0


def build_snapshot_runtime_config(
    snapshot_dir: Path,
    *,
    system_name: str,
    stale_after_seconds: int,
) -> TransitRuntimeConfig:
    manifest = load_snapshot_manifest(snapshot_dir)
    case_pack_root = case_pack_root_for_snapshot(snapshot_dir)
    event_overlay_path = resolve_case_pack_event_overlay_path(case_pack_root) if case_pack_root else None
    feed_paths = resolve_snapshot_feed_paths(snapshot_dir)
    agency_key = agency_key_for_snapshot(snapshot_dir, manifest=manifest)
    adapter = get_transit_agency_adapter(agency_key)
    return TransitRuntimeConfig(
        system_name=str(manifest.get("agency") or system_name or adapter.system_name),
        agency_key=adapter.key,
        static_feed=optional_existing_path(feed_paths.get("static_gtfs")),
        vehicle_positions_feed=optional_existing_path(feed_paths.get("vehicle_positions")),
        trip_updates_feed=optional_existing_path(feed_paths.get("trip_updates")),
        alerts_feed=optional_existing_path(feed_paths.get("alerts")),
        event_overlays_feed=str(event_overlay_path) if event_overlay_path else None,
        stale_after_seconds=stale_after_seconds,
        feed_timezone=str(manifest.get("feed_timezone") or adapter.timezone_name),
    )


def discover_case_pack_roots(root: Path) -> List[Path]:
    catalog_root = Path(root).resolve()
    if (catalog_root / CASE_PACK_METADATA_FILENAME).exists():
        return [catalog_root]
    roots = sorted({path.parent.resolve() for path in catalog_root.rglob(CASE_PACK_METADATA_FILENAME)})
    if roots:
        return roots
    raise FileNotFoundError(f"no case packs found under {catalog_root}")


def case_pack_root_for_snapshot(snapshot_dir: Path) -> Optional[Path]:
    current = Path(snapshot_dir).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / CASE_PACK_METADATA_FILENAME).exists():
            return candidate
    return None


def latest_snapshot_dir(case_pack_root: Path) -> Path:
    snapshot_dirs = discover_snapshot_dirs(case_pack_root)
    if not snapshot_dirs:
        raise RuntimeError(f"no archived snapshots found under {case_pack_root}")
    return snapshot_dirs[-1]


def agency_key_for_snapshot(snapshot_dir: Path, *, manifest: Optional[Dict[str, Any]] = None) -> str:
    payload = manifest or load_snapshot_manifest(snapshot_dir)
    explicit = str(payload.get("agency_key") or "").strip().lower()
    if explicit:
        return explicit
    agency = str(payload.get("agency") or "").strip().lower()
    if agency == "mbta":
        return "mbta"
    if agency in {"la metro rail", "los angeles metro rail"}:
        return "lametro-rail"
    if agency in {"la metro bus", "los angeles metro bus"}:
        return "lametro-bus"
    case_pack_root = case_pack_root_for_snapshot(snapshot_dir)
    if case_pack_root:
        metadata = load_case_pack_metadata(case_pack_root)
        return first_agency_key(metadata)
    return default_transit_agency_key()


def first_agency_key(metadata: Dict[str, Any]) -> str:
    agency_keys = [str(value).strip() for value in (metadata.get("agency_keys") or []) if str(value).strip()]
    return agency_keys[0] if agency_keys else default_transit_agency_key()


def trace_id_for_case_pack(case_pack_id: str, *, prefix: str = "") -> str:
    normalized_case_pack_id = str(case_pack_id or "").strip()
    normalized_prefix = str(prefix or "").strip()
    if not normalized_prefix:
        return normalized_case_pack_id
    return f"{normalized_prefix}-{normalized_case_pack_id}"


def trace_id_for_archive_root(archive_root: Path, *, agency_key: str, prefix: str = "") -> str:
    resolved = Path(archive_root).resolve()
    base = agency_key or resolved.name or "archive"
    return trace_id_for_case_pack(f"{base}-recent", prefix=prefix)


def snapshot_path_label(snapshot_dir: Path, *, root: Path) -> str:
    manifest = load_snapshot_manifest(snapshot_dir)
    explicit = str(manifest.get("snapshot_path") or "").strip()
    if explicit:
        return explicit
    try:
        return str(snapshot_dir.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(snapshot_dir.resolve())


def optional_existing_path(value: str | None) -> Optional[str]:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    path = Path(candidate)
    return str(path) if path.exists() else None


def optional_path(value: str | None) -> Optional[Path]:
    text = str(value or "").strip()
    return Path(text).expanduser().resolve() if text else None


def discover_demo_archive_roots() -> List[Path]:
    roots: List[Path] = []
    for agency_key in ["mbta", "lametro-rail", "lametro-bus"]:
        adapter = get_transit_agency_adapter(agency_key)
        archive_root = adapter.archive_root_path().resolve()
        if latest_snapshot_dir_or_none(archive_root):
            roots.append(archive_root)
    return roots


def default_live_seed_sources() -> Dict[str, Optional[Path]]:
    mbta_archive_root = get_transit_agency_adapter("mbta").archive_root_path().resolve()
    snapshot_dir = latest_snapshot_dir_or_none(mbta_archive_root)
    if snapshot_dir is not None:
        return {
            "case_pack_root": None,
            "archive_root": mbta_archive_root,
            "snapshot_dir": snapshot_dir,
        }
    overnight_case_pack = Path(
        "data/case-packs/mbta/overnight_advisory_controls"
    ).resolve()
    return {
        "case_pack_root": overnight_case_pack if overnight_case_pack.exists() else None,
        "archive_root": None,
        "snapshot_dir": None,
    }


def latest_snapshot_dir_or_none(root: Path) -> Optional[Path]:
    try:
        return latest_snapshot_dir(root)
    except RuntimeError:
        return None


def archive_agency_key(archive_root: Path, manifest: Optional[Dict[str, Any]] = None) -> str:
    payload = manifest or {}
    explicit = str(payload.get("agency_key") or "").strip().lower()
    if explicit:
        return explicit
    for agency_key in ["mbta", "lametro-rail", "lametro-bus"]:
        adapter = get_transit_agency_adapter(agency_key)
        if Path(archive_root).resolve() == adapter.archive_root_path().resolve():
            return adapter.key
    return agency_key_for_snapshot(Path(archive_root), manifest=payload) if payload else default_transit_agency_key()


if __name__ == "__main__":
    raise SystemExit(main())
