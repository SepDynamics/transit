#!/usr/bin/env python3
"""Persist current transit feeds into the rolling Valkey store."""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.shared.runtime import isoformat_ms
from scripts.transit.agencies import default_transit_agency_key, get_transit_agency_adapter
from scripts.transit.domain import TransitRuntimeConfig, TransitSnapshotService
from scripts.transit.store import TransitStore

logger = logging.getLogger("transit-ingest")


@dataclass
class TransitIngestConfig:
    redis_url: str
    interval_seconds: float
    history_retention: int
    runtime: TransitRuntimeConfig


class TransitIngestService:
    def __init__(self, config: TransitIngestConfig, *, store: Optional[TransitStore] = None) -> None:
        self.cfg = config
        self.store = store or TransitStore(config.redis_url)
        self.snapshot_service = TransitSnapshotService(config.runtime)
        self._stop = False

    def run(self) -> None:
        logger.info("Transit ingest service starting for system=%s", self.cfg.runtime.system_name)
        while not self._stop:
            started = time.time()
            try:
                self.run_once()
            except Exception:
                logger.exception("transit ingest iteration failed")
            elapsed = time.time() - started
            time.sleep(max(0.2, self.cfg.interval_seconds - elapsed))

    def run_once(self) -> Dict[str, Any]:
        payload = self.snapshot_service.snapshot()
        configured_feeds = {
            "static_gtfs": bool(self.cfg.runtime.static_feed),
            "vehicle_positions": bool(self.cfg.runtime.vehicle_positions_feed),
            "trip_updates": bool(self.cfg.runtime.trip_updates_feed),
            "alerts": bool(self.cfg.runtime.alerts_feed),
            "event_overlays": bool(self.cfg.runtime.event_overlays_feed),
        }
        self.store.write_snapshot(payload, configured_feeds=configured_feeds, retention=self.cfg.history_retention)
        status = {
            "system_name": self.cfg.runtime.system_name,
            "agency_key": self.cfg.runtime.agency_key,
            "status": "ok" if not (payload.get("errors") or []) else "degraded",
            "updated_at": isoformat_ms(),
            "feed_status": payload.get("feed_status") or {},
            "errors": list(payload.get("errors") or []),
        }
        manifest = _load_current_manifest(self.cfg.runtime.static_feed)
        if manifest:
            status["archive_manifest"] = manifest
        self.store.write_status("ops:transit_ingest_status", status)
        logger.info(
            "persisted transit snapshot with %d vehicles and %d incidents",
            len((payload.get("entities") or {}).get("vehicles") or []),
            len((payload.get("incidents") or {}).get("incidents") or []),
        )
        return payload

    def stop(self) -> None:
        self._stop = True


def build_parser() -> argparse.ArgumentParser:
    adapter = get_transit_agency_adapter(os.getenv("TRANSIT_AGENCY", default_transit_agency_key()))
    default_feed_paths = adapter.default_feed_paths()
    parser = argparse.ArgumentParser(description="Persist current transit feeds into Valkey")
    parser.add_argument("--redis", default=os.getenv("VALKEY_URL", "redis://localhost:6379/0"))
    parser.add_argument("--interval", type=float, default=float(os.getenv("TRANSIT_INGEST_INTERVAL_SECONDS", "5")))
    parser.add_argument("--history-retention", type=int, default=int(os.getenv("TRANSIT_HISTORY_RETENTION", "720")))
    parser.add_argument("--agency", default=os.getenv("TRANSIT_AGENCY", adapter.key))
    parser.add_argument("--system-name", default=os.getenv("TRANSIT_SYSTEM_NAME", adapter.system_name))
    parser.add_argument("--static-feed", default=os.getenv("TRANSIT_GTFS_STATIC_PATH", default_feed_paths["static_gtfs"]))
    parser.add_argument(
        "--vehicle-positions-feed",
        default=os.getenv("TRANSIT_GTFS_RT_VEHICLE_POSITIONS_PATH", default_feed_paths["vehicle_positions"]),
    )
    parser.add_argument(
        "--trip-updates-feed",
        default=os.getenv("TRANSIT_GTFS_RT_TRIP_UPDATES_PATH", default_feed_paths["trip_updates"]),
    )
    parser.add_argument(
        "--alerts-feed",
        default=os.getenv("TRANSIT_GTFS_RT_ALERTS_PATH", default_feed_paths["alerts"]),
    )
    parser.add_argument("--event-overlays-feed", default=os.getenv("TRANSIT_EVENT_OVERLAYS_PATH", ""))
    parser.add_argument("--stale-after-seconds", type=int, default=int(os.getenv("TRANSIT_FEED_STALE_AFTER_SECONDS", "90")))
    parser.add_argument("--feed-timezone", default=os.getenv("TRANSIT_FEED_TIMEZONE", adapter.timezone_name))
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
    args = build_parser().parse_args()
    adapter = get_transit_agency_adapter(str(args.agency))
    cfg = TransitIngestConfig(
        redis_url=str(args.redis),
        interval_seconds=max(1.0, float(args.interval)),
        history_retention=max(12, int(args.history_retention)),
        runtime=TransitRuntimeConfig(
            system_name=str(args.system_name or adapter.system_name),
            agency_key=adapter.key,
            static_feed=_optional_path(args.static_feed),
            vehicle_positions_feed=_optional_path(args.vehicle_positions_feed),
            trip_updates_feed=_optional_path(args.trip_updates_feed),
            alerts_feed=_optional_path(args.alerts_feed),
            event_overlays_feed=_optional_path(args.event_overlays_feed),
            stale_after_seconds=max(30, int(args.stale_after_seconds)),
            feed_timezone=str(args.feed_timezone or adapter.timezone_name),
        ),
    )
    service = TransitIngestService(cfg)

    def _handle_signal(signum: int, _frame: Any) -> None:
        logger.info("received signal %s, stopping transit ingest", signum)
        service.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.once:
        service.run_once()
        return 0
    service.run()
    return 0


def _optional_path(value: str | None) -> Optional[str]:
    value = str(value or "").strip()
    return value or None


def _load_current_manifest(static_feed: Optional[str]) -> Dict[str, Any]:
    if not static_feed:
        return {}
    current_dir = Path(static_feed).expanduser().resolve().parent
    manifest_path = current_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
