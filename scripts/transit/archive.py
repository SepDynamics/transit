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

logger = logging.getLogger("transit-archive")

MBTA_STATIC_GTFS_URL = "https://cdn.mbta.com/MBTA_GTFS.zip"
MBTA_VEHICLE_POSITIONS_URL = "https://cdn.mbta.com/realtime/VehiclePositions_enhanced.json"
MBTA_TRIP_UPDATES_URL = "https://cdn.mbta.com/realtime/TripUpdates_enhanced.json"
MBTA_ALERTS_URL = "https://cdn.mbta.com/realtime/Alerts_enhanced.json"


@dataclass(frozen=True)
class FeedTarget:
    name: str
    url: str
    filename: str
    binary: bool = False
    static: bool = False


@dataclass
class MBTAArchiveConfig:
    root_dir: Path
    interval_seconds: float
    timeout_seconds: float
    static_refresh_seconds: int
    static_url: str
    vehicle_positions_url: str
    trip_updates_url: str
    alerts_url: str


class MBTAArchiveService:
    def __init__(self, config: MBTAArchiveConfig, *, session: Optional[requests.Session] = None) -> None:
        self.cfg = config
        self.session = session or requests.Session()
        self._stop = False

    def run(self) -> None:
        logger.info("MBTA archive service starting at root=%s", self.cfg.root_dir)
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
            FeedTarget("static_gtfs", self.cfg.static_url, "MBTA_GTFS.zip", binary=True, static=True),
            FeedTarget("vehicle_positions", self.cfg.vehicle_positions_url, "VehiclePositions_enhanced.json"),
            FeedTarget("trip_updates", self.cfg.trip_updates_url, "TripUpdates_enhanced.json"),
            FeedTarget("alerts", self.cfg.alerts_url, "Alerts_enhanced.json"),
        ]

        manifest_feeds: List[Dict[str, Any]] = []
        for feed in feeds:
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
            "agency": "MBTA",
            "captured_at": isoformat_ms(timestamp_ms),
            "timestamp_ms": timestamp_ms,
            "snapshot_path": str(snapshot_dir.relative_to(self.cfg.root_dir)),
            "feeds": manifest_feeds,
        }
        _write_json(snapshot_dir / "manifest.json", manifest)
        _write_json(current_dir / "manifest.json", manifest)
        logger.info("archived MBTA snapshot with %d feed results", len(manifest_feeds))
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
    parser = argparse.ArgumentParser(description="Archive MBTA GTFS and GTFS-RT feeds locally")
    parser.add_argument("--root-dir", default=os.getenv("TRANSIT_ARCHIVE_ROOT", "data/feeds/mbta"))
    parser.add_argument("--interval", type=float, default=float(os.getenv("TRANSIT_ARCHIVE_INTERVAL_SECONDS", "30")))
    parser.add_argument("--timeout-seconds", type=float, default=float(os.getenv("TRANSIT_ARCHIVE_TIMEOUT_SECONDS", "30")))
    parser.add_argument("--static-refresh-seconds", type=int, default=int(os.getenv("TRANSIT_STATIC_REFRESH_SECONDS", "21600")))
    parser.add_argument("--static-url", default=os.getenv("MBTA_STATIC_GTFS_URL", MBTA_STATIC_GTFS_URL))
    parser.add_argument("--vehicle-positions-url", default=os.getenv("MBTA_VEHICLE_POSITIONS_URL", MBTA_VEHICLE_POSITIONS_URL))
    parser.add_argument("--trip-updates-url", default=os.getenv("MBTA_TRIP_UPDATES_URL", MBTA_TRIP_UPDATES_URL))
    parser.add_argument("--alerts-url", default=os.getenv("MBTA_ALERTS_URL", MBTA_ALERTS_URL))
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
    args = build_parser().parse_args()
    cfg = MBTAArchiveConfig(
        root_dir=Path(args.root_dir).resolve(),
        interval_seconds=max(1.0, float(args.interval)),
        timeout_seconds=max(1.0, float(args.timeout_seconds)),
        static_refresh_seconds=max(60, int(args.static_refresh_seconds)),
        static_url=str(args.static_url),
        vehicle_positions_url=str(args.vehicle_positions_url),
        trip_updates_url=str(args.trip_updates_url),
        alerts_url=str(args.alerts_url),
    )
    service = MBTAArchiveService(cfg)

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
