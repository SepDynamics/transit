#!/usr/bin/env python3
"""Archive official transit feeds into a local current/ and timestamped store."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.shared.runtime import isoformat_ms
from scripts.transit.agencies import default_transit_agency_key, get_transit_agency_adapter

logger = logging.getLogger("transit-archive")


@dataclass(frozen=True)
class FeedTarget:
    name: str
    url: str
    filename: str
    binary: bool = False
    static: bool = False


@dataclass
class TransitAgencyArchiveConfig:
    agency_key: str = default_transit_agency_key()
    system_name: str = "MBTA"
    root_dir: Path = Path("data/feeds/mbta")
    interval_seconds: float = 30.0
    timeout_seconds: float = 30.0
    static_refresh_seconds: int = 21600
    static_filename: str = "MBTA_GTFS.zip"
    vehicle_positions_filename: str = "VehiclePositions_enhanced.json"
    trip_updates_filename: str = "TripUpdates_enhanced.json"
    alerts_filename: str = "Alerts_enhanced.json"
    static_url: Optional[str] = None
    vehicle_positions_url: Optional[str] = None
    trip_updates_url: Optional[str] = None
    alerts_url: Optional[str] = None


class TransitAgencyArchiveService:
    def __init__(self, config: TransitAgencyArchiveConfig, *, session: Optional[requests.Session] = None) -> None:
        self.cfg = config
        self.session = session or requests.Session()
        self._stop = False

    def run(self) -> None:
        logger.info("Transit archive service starting for agency=%s root=%s", self.cfg.agency_key, self.cfg.root_dir)
        while not self._stop:
            started = time.time()
            try:
                self.run_once()
            except Exception:
                logger.exception("archive iteration failed")
            elapsed = time.time() - started
            time.sleep(max(0.2, self.cfg.interval_seconds - elapsed))

    def run_once(self) -> Dict[str, Any]:
        timestamp_ms = int(time.time() * 1000)
        snapshot_dir = self._snapshot_dir(timestamp_ms)
        current_dir = self.cfg.root_dir / "current"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        current_dir.mkdir(parents=True, exist_ok=True)

        feeds = [
            FeedTarget("static_gtfs", self.cfg.static_url, self.cfg.static_filename, binary=True, static=True),
            FeedTarget("vehicle_positions", self.cfg.vehicle_positions_url, self.cfg.vehicle_positions_filename),
            FeedTarget("trip_updates", self.cfg.trip_updates_url, self.cfg.trip_updates_filename),
            FeedTarget("alerts", self.cfg.alerts_url, self.cfg.alerts_filename),
        ]

        manifest_feeds: List[Dict[str, Any]] = []
        for feed in feeds:
            if not feed.url:
                continue
            if feed.static and not self._should_refresh_static(current_dir / feed.filename, timestamp_ms):
                manifest_feeds.append(self._reuse_static_feed(feed, current_dir / feed.filename, snapshot_dir, timestamp_ms))
                continue
            fetched = self._fetch_feed(feed)
            current_path = current_dir / feed.filename
            snapshot_path = snapshot_dir / feed.filename
            _atomic_write(current_path, fetched["content"], binary=feed.binary)
            _atomic_write(snapshot_path, fetched["content"], binary=feed.binary)
            meta = {
                "name": feed.name,
                "url": feed.url,
                "filename": feed.filename,
                "captured_at": isoformat_ms(timestamp_ms),
                "timestamp_ms": timestamp_ms,
                "sha256": fetched["sha256"],
                "etag": fetched["etag"],
                "last_modified": fetched["last_modified"],
                "content_type": fetched["content_type"],
                "content_length": fetched["content_length"],
                "status": "archived",
                "path": str(snapshot_path.relative_to(self.cfg.root_dir)),
            }
            _write_json(current_dir / f"{feed.filename}.meta.json", meta)
            _write_json(snapshot_dir / f"{feed.filename}.meta.json", meta)
            manifest_feeds.append(meta)

        manifest = {
            "agency": self.cfg.system_name,
            "agency_key": self.cfg.agency_key,
            "captured_at": isoformat_ms(timestamp_ms),
            "timestamp_ms": timestamp_ms,
            "snapshot_path": str(snapshot_dir.relative_to(self.cfg.root_dir)),
            "feeds": manifest_feeds,
        }
        _write_json(snapshot_dir / "manifest.json", manifest)
        _write_json(current_dir / "manifest.json", manifest)
        logger.info("archived transit snapshot for agency=%s with %d feed results", self.cfg.agency_key, len(manifest_feeds))
        return manifest

    def stop(self) -> None:
        self._stop = True

    def _fetch_feed(self, feed: FeedTarget) -> Dict[str, Any]:
        response = self.session.get(feed.url, timeout=self.cfg.timeout_seconds)
        response.raise_for_status()
        content = response.content
        return {
            "content": content,
            "sha256": hashlib.sha256(content).hexdigest(),
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
            "content_type": response.headers.get("Content-Type"),
            "content_length": len(content),
        }

    def _should_refresh_static(self, current_path: Path, timestamp_ms: int) -> bool:
        if not current_path.exists():
            return True
        age_seconds = max(0.0, (timestamp_ms - int(current_path.stat().st_mtime * 1000)) / 1000.0)
        return age_seconds >= max(60, self.cfg.static_refresh_seconds)

    def _snapshot_dir(self, timestamp_ms: int) -> Path:
        stamp = time.gmtime(timestamp_ms / 1000.0)
        return self.cfg.root_dir / "archive" / time.strftime("%Y", stamp) / time.strftime("%m", stamp) / time.strftime("%d", stamp) / time.strftime("%H%M%SZ", stamp)

    def _reuse_static_feed(self, feed: FeedTarget, current_path: Path, snapshot_dir: Path, timestamp_ms: int) -> Dict[str, Any]:
        archived_path = self._latest_archived_feed(feed.filename)
        resolved_path = archived_path
        status = "reused_archived_static"
        if resolved_path is None:
            resolved_path = snapshot_dir / feed.filename
            _atomic_write(resolved_path, current_path.read_bytes(), binary=feed.binary)
            status = "archived_from_current"
        return {
            "name": feed.name,
            "url": feed.url,
            "filename": feed.filename,
            "captured_at": isoformat_ms(timestamp_ms),
            "timestamp_ms": timestamp_ms,
            "status": status,
            "path": str(resolved_path.relative_to(self.cfg.root_dir)),
        }

    def _latest_archived_feed(self, filename: str) -> Optional[Path]:
        candidates = sorted(path for path in self.cfg.root_dir.glob(f"archive/*/*/*/*/{filename}") if path.is_file())
        return candidates[-1] if candidates else None


def build_parser() -> argparse.ArgumentParser:
    adapter = get_transit_agency_adapter(os.getenv("TRANSIT_AGENCY", default_transit_agency_key()))
    parser = argparse.ArgumentParser(description="Archive GTFS and GTFS-RT feeds locally for a configured transit agency")
    parser.add_argument("--agency", default=os.getenv("TRANSIT_AGENCY", adapter.key))
    parser.add_argument("--root-dir", default=os.getenv("TRANSIT_ARCHIVE_ROOT", adapter.archive_root))
    parser.add_argument("--interval", type=float, default=float(os.getenv("TRANSIT_ARCHIVE_INTERVAL_SECONDS", "30")))
    parser.add_argument("--timeout-seconds", type=float, default=float(os.getenv("TRANSIT_ARCHIVE_TIMEOUT_SECONDS", "30")))
    parser.add_argument("--static-refresh-seconds", type=int, default=int(os.getenv("TRANSIT_STATIC_REFRESH_SECONDS", "21600")))
    parser.add_argument("--system-name", default=os.getenv("TRANSIT_SYSTEM_NAME", adapter.system_name))
    parser.add_argument("--static-filename", default=os.getenv("TRANSIT_STATIC_FILENAME", adapter.static_feed_filename))
    parser.add_argument("--vehicle-positions-filename", default=os.getenv("TRANSIT_VEHICLE_POSITIONS_FILENAME", adapter.vehicle_positions_filename))
    parser.add_argument("--trip-updates-filename", default=os.getenv("TRANSIT_TRIP_UPDATES_FILENAME", adapter.trip_updates_filename))
    parser.add_argument("--alerts-filename", default=os.getenv("TRANSIT_ALERTS_FILENAME", adapter.alerts_filename))
    parser.add_argument("--static-url", default=os.getenv("TRANSIT_STATIC_GTFS_URL", adapter.static_url or ""))
    parser.add_argument("--vehicle-positions-url", default=os.getenv("TRANSIT_VEHICLE_POSITIONS_URL", adapter.vehicle_positions_url or ""))
    parser.add_argument("--trip-updates-url", default=os.getenv("TRANSIT_TRIP_UPDATES_URL", adapter.trip_updates_url or ""))
    parser.add_argument("--alerts-url", default=os.getenv("TRANSIT_ALERTS_URL", adapter.alerts_url or ""))
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
    args = build_parser().parse_args()
    adapter = get_transit_agency_adapter(str(args.agency))
    cfg = TransitAgencyArchiveConfig(
        agency_key=adapter.key,
        system_name=str(args.system_name or adapter.system_name),
        root_dir=Path(args.root_dir).resolve(),
        interval_seconds=max(1.0, float(args.interval)),
        timeout_seconds=max(1.0, float(args.timeout_seconds)),
        static_refresh_seconds=max(60, int(args.static_refresh_seconds)),
        static_filename=str(args.static_filename or adapter.static_feed_filename),
        vehicle_positions_filename=str(args.vehicle_positions_filename or adapter.vehicle_positions_filename),
        trip_updates_filename=str(args.trip_updates_filename or adapter.trip_updates_filename),
        alerts_filename=str(args.alerts_filename or adapter.alerts_filename),
        static_url=_optional_url(args.static_url),
        vehicle_positions_url=_optional_url(args.vehicle_positions_url),
        trip_updates_url=_optional_url(args.trip_updates_url),
        alerts_url=_optional_url(args.alerts_url),
    )
    service = TransitAgencyArchiveService(cfg)

    def _handle_signal(signum: int, _frame: Any) -> None:
        logger.info("received signal %s, stopping archive service", signum)
        service.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.once:
        service.run_once()
        return 0
    service.run()
    return 0


def _atomic_write(path: Path, content: bytes, *, binary: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    if binary:
        tmp_path.write_bytes(content)
    else:
        tmp_path.write_text(content.decode("utf-8"), encoding="utf-8")
    tmp_path.replace(path)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())


def _optional_url(value: str | None) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


MBTAArchiveConfig = TransitAgencyArchiveConfig
MBTAArchiveService = TransitAgencyArchiveService
