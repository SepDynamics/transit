#!/usr/bin/env python3
"""Archive official transit feeds into a local current/ and timestamped store."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.shared.runtime import isoformat_ms
from scripts.transit.agencies import (
    default_transit_agency_key,
    get_transit_agency_adapter,
)
from scripts.transit.feeds import validate_gtfs_realtime_payload

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
    write_history: bool = True
    retention_days: int = 0


class TransitAgencyArchiveService:
    def __init__(
        self,
        config: TransitAgencyArchiveConfig,
        *,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.cfg = config
        self.session = session or requests.Session()
        self._stop = False

    def run(self) -> None:
        logger.info(
            "Transit archive service starting for agency=%s root=%s",
            self.cfg.agency_key,
            self.cfg.root_dir,
        )
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
        snapshot_dir = (
            self._snapshot_dir(timestamp_ms) if self.cfg.write_history else None
        )
        current_dir = self.cfg.root_dir / "current"
        if snapshot_dir is not None:
            snapshot_dir.mkdir(parents=True, exist_ok=True)
        current_dir.mkdir(parents=True, exist_ok=True)

        feeds = [
            FeedTarget(
                "static_gtfs",
                self.cfg.static_url,
                self.cfg.static_filename,
                binary=True,
                static=True,
            ),
            FeedTarget(
                "vehicle_positions",
                self.cfg.vehicle_positions_url,
                self.cfg.vehicle_positions_filename,
            ),
            FeedTarget(
                "trip_updates",
                self.cfg.trip_updates_url,
                self.cfg.trip_updates_filename,
            ),
            FeedTarget("alerts", self.cfg.alerts_url, self.cfg.alerts_filename),
        ]

        manifest_feeds: List[Dict[str, Any]] = []
        for feed in feeds:
            if not feed.url:
                continue
            if feed.static and not self._should_refresh_static(
                current_dir / feed.filename, timestamp_ms
            ):
                if snapshot_dir is not None:
                    manifest_feeds.append(
                        self._reuse_static_feed(
                            feed,
                            current_dir / feed.filename,
                            snapshot_dir,
                            timestamp_ms,
                        )
                    )
                else:
                    manifest_feeds.append(
                        self._reuse_current_static_feed(
                            feed, current_dir / feed.filename, timestamp_ms
                        )
                    )
                continue
            try:
                fetched = self._fetch_feed(feed)
                self._validate_feed(feed, fetched)
            except Exception as exc:
                manifest_feeds.append(
                    self._preserve_previous_feed(feed, current_dir, timestamp_ms, exc)
                )
                logger.warning(
                    "rejected %s feed for agency=%s; previous good state preserved: %s",
                    feed.name,
                    self.cfg.agency_key,
                    exc,
                )
                continue
            current_path = current_dir / feed.filename
            _atomic_write(current_path, fetched["content"], binary=feed.binary)
            if snapshot_dir is not None:
                feed_path = snapshot_dir / feed.filename
                _atomic_write(feed_path, fetched["content"], binary=feed.binary)
                status = "archived"
            else:
                feed_path = current_path
                status = "current"
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
                "content_encoding": fetched["content_encoding"],
                "status": status,
                "path": str(feed_path.relative_to(self.cfg.root_dir)),
            }
            _write_json(current_dir / f"{feed.filename}.meta.json", meta)
            if snapshot_dir is not None:
                _write_json(snapshot_dir / f"{feed.filename}.meta.json", meta)
            manifest_feeds.append(meta)

        manifest = {
            "agency": self.cfg.system_name,
            "agency_key": self.cfg.agency_key,
            "captured_at": isoformat_ms(timestamp_ms),
            "timestamp_ms": timestamp_ms,
            "snapshot_path": str(snapshot_dir.relative_to(self.cfg.root_dir))
            if snapshot_dir is not None
            else "current",
            "history_enabled": snapshot_dir is not None,
            "feeds": manifest_feeds,
        }
        if snapshot_dir is not None:
            _write_json(snapshot_dir / "manifest.json", manifest)
        _write_json(current_dir / "manifest.json", manifest)
        if snapshot_dir is not None and self.cfg.retention_days > 0:
            retention = prune_archive_history(
                self.cfg.root_dir,
                now_ms=timestamp_ms,
                retention_days=self.cfg.retention_days,
            )
            manifest["retention"] = retention
            _write_json(snapshot_dir / "manifest.json", manifest)
            _write_json(current_dir / "manifest.json", manifest)
        logger.info(
            "refreshed transit feeds for agency=%s with %d feed results history=%s",
            self.cfg.agency_key,
            len(manifest_feeds),
            "enabled" if snapshot_dir is not None else "disabled",
        )
        return manifest

    def stop(self) -> None:
        self._stop = True

    def _fetch_feed(self, feed: FeedTarget) -> Dict[str, Any]:
        headers = self._feed_request_headers(feed.url)
        request_kwargs: Dict[str, Any] = {"timeout": self.cfg.timeout_seconds}
        if headers:
            request_kwargs["headers"] = headers
        response = self.session.get(feed.url, **request_kwargs)
        response.raise_for_status()
        content = response.content
        return {
            "content": content,
            "sha256": hashlib.sha256(content).hexdigest(),
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
            "content_type": response.headers.get("Content-Type"),
            "content_length": len(content),
            "content_encoding": response.headers.get("Content-Encoding"),
        }

    def _validate_feed(self, feed: FeedTarget, fetched: Dict[str, Any]) -> None:
        content = bytes(fetched["content"])
        if not content:
            raise ValueError("empty response")
        if feed.static:
            if not content.startswith(b"PK\x03\x04"):
                raise ValueError("static GTFS response is not a zip archive")
            return
        validate_gtfs_realtime_payload(
            content,
            content_type=fetched.get("content_type"),
        )

    def _preserve_previous_feed(
        self, feed: FeedTarget, current_dir: Path, timestamp_ms: int, exc: Exception
    ) -> Dict[str, Any]:
        current_path = current_dir / feed.filename
        previous_meta = _read_json(current_dir / f"{feed.filename}.meta.json")
        return {
            "name": feed.name,
            "url": feed.url,
            "filename": feed.filename,
            "captured_at": isoformat_ms(timestamp_ms),
            "timestamp_ms": timestamp_ms,
            "status": "degraded_preserved" if current_path.exists() else "failed_no_previous",
            "path": str(current_path.relative_to(self.cfg.root_dir)) if current_path.exists() else None,
            "previous_sha256": previous_meta.get("sha256"),
            "error": str(exc),
        }

    def _should_refresh_static(self, current_path: Path, timestamp_ms: int) -> bool:
        if not current_path.exists():
            return True
        age_seconds = max(
            0.0, (timestamp_ms - int(current_path.stat().st_mtime * 1000)) / 1000.0
        )
        return age_seconds >= max(60, self.cfg.static_refresh_seconds)

    def _snapshot_dir(self, timestamp_ms: int) -> Path:
        stamp = time.gmtime(timestamp_ms / 1000.0)
        return (
            self.cfg.root_dir
            / "archive"
            / time.strftime("%Y", stamp)
            / time.strftime("%m", stamp)
            / time.strftime("%d", stamp)
            / time.strftime("%H%M%SZ", stamp)
        )

    def _reuse_static_feed(
        self,
        feed: FeedTarget,
        current_path: Path,
        snapshot_dir: Path,
        timestamp_ms: int,
    ) -> Dict[str, Any]:
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

    def _reuse_current_static_feed(
        self, feed: FeedTarget, current_path: Path, timestamp_ms: int
    ) -> Dict[str, Any]:
        return {
            "name": feed.name,
            "url": feed.url,
            "filename": feed.filename,
            "captured_at": isoformat_ms(timestamp_ms),
            "timestamp_ms": timestamp_ms,
            "status": "reused_current_static",
            "path": str(current_path.relative_to(self.cfg.root_dir)),
        }

    @staticmethod
    def _feed_request_headers(url: str) -> Dict[str, str]:
        """Return HTTP headers needed for a given feed URL.

        Swiftly endpoints require an Authorization header with the API key.
        """
        headers: Dict[str, str] = {}
        if "goswift.ly" in str(url).lower():
            api_key = os.getenv("SWIFTLY_API_KEY", "").strip()
            if api_key:
                headers["Authorization"] = api_key
        return headers

    def _latest_archived_feed(self, filename: str) -> Optional[Path]:
        candidates = sorted(
            path
            for path in self.cfg.root_dir.glob(f"archive/*/*/*/*/{filename}")
            if path.is_file()
        )
        return candidates[-1] if candidates else None


def build_parser() -> argparse.ArgumentParser:
    adapter = get_transit_agency_adapter(
        os.getenv("TRANSIT_AGENCY", default_transit_agency_key())
    )
    parser = argparse.ArgumentParser(
        description="Archive GTFS and GTFS-RT feeds locally for a configured transit agency"
    )
    parser.add_argument("--agency", default=os.getenv("TRANSIT_AGENCY", adapter.key))
    parser.add_argument(
        "--root-dir", default=os.getenv("TRANSIT_ARCHIVE_ROOT", adapter.archive_root)
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.getenv("TRANSIT_ARCHIVE_INTERVAL_SECONDS", "30")),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("TRANSIT_ARCHIVE_TIMEOUT_SECONDS", "30")),
    )
    parser.add_argument(
        "--static-refresh-seconds",
        type=int,
        default=int(os.getenv("TRANSIT_STATIC_REFRESH_SECONDS", "21600")),
    )
    parser.add_argument(
        "--current-only",
        action="store_true",
        default=_truthy_env("TRANSIT_ARCHIVE_CURRENT_ONLY"),
        help="Refresh only current feed files and skip timestamped archive windows",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=int(os.getenv("TRANSIT_ARCHIVE_RETENTION_DAYS", "0")),
        help=(
            "delete timestamped snapshots older than this many days after a "
            "successful history capture; 0 disables pruning"
        ),
    )
    parser.add_argument(
        "--system-name", default=os.getenv("TRANSIT_SYSTEM_NAME", adapter.system_name)
    )
    parser.add_argument(
        "--static-filename",
        default=os.getenv("TRANSIT_STATIC_FILENAME", adapter.static_feed_filename),
    )
    parser.add_argument(
        "--vehicle-positions-filename",
        default=os.getenv(
            "TRANSIT_VEHICLE_POSITIONS_FILENAME", adapter.vehicle_positions_filename
        ),
    )
    parser.add_argument(
        "--trip-updates-filename",
        default=os.getenv(
            "TRANSIT_TRIP_UPDATES_FILENAME", adapter.trip_updates_filename
        ),
    )
    parser.add_argument(
        "--alerts-filename",
        default=os.getenv("TRANSIT_ALERTS_FILENAME", adapter.alerts_filename),
    )
    parser.add_argument(
        "--static-url",
        default=os.getenv("TRANSIT_STATIC_GTFS_URL", adapter.static_url or ""),
    )
    parser.add_argument(
        "--vehicle-positions-url",
        default=os.getenv(
            "TRANSIT_VEHICLE_POSITIONS_URL", adapter.vehicle_positions_url or ""
        ),
    )
    parser.add_argument(
        "--trip-updates-url",
        default=os.getenv("TRANSIT_TRIP_UPDATES_URL", adapter.trip_updates_url or ""),
    )
    parser.add_argument(
        "--alerts-url",
        default=os.getenv("TRANSIT_ALERTS_URL", adapter.alerts_url or ""),
    )
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
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
        vehicle_positions_filename=str(
            args.vehicle_positions_filename or adapter.vehicle_positions_filename
        ),
        trip_updates_filename=str(
            args.trip_updates_filename or adapter.trip_updates_filename
        ),
        alerts_filename=str(args.alerts_filename or adapter.alerts_filename),
        static_url=_optional_url(args.static_url),
        vehicle_positions_url=_optional_url(args.vehicle_positions_url),
        trip_updates_url=_optional_url(args.trip_updates_url),
        alerts_url=_optional_url(args.alerts_url),
        write_history=not bool(args.current_only),
        retention_days=max(0, int(args.retention_days)),
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
        try:
            tmp_path.write_text(content.decode("utf-8"), encoding="utf-8")
        except UnicodeDecodeError:
            # Content is binary despite text mode hint; write as binary
            tmp_path.write_bytes(content)
    tmp_path.replace(path)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _optional_url(value: str | None) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _truthy_env(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def prune_archive_history(
    root_dir: str | Path,
    *,
    now_ms: int,
    retention_days: int,
) -> Dict[str, Any]:
    """Delete expired timestamped snapshots without breaking retained manifests.

    Static GTFS files are intentionally reused across snapshot directories.  An
    otherwise-expired directory remains protected while a retained manifest
    references a file inside it.  This keeps every retained replay window
    resolvable and limits the exception to static-feed anchor snapshots.
    """

    retention_days = max(0, int(retention_days))
    cutoff_ms = int(now_ms) - (retention_days * 24 * 60 * 60 * 1000)
    report: Dict[str, Any] = {
        "enabled": retention_days > 0,
        "retention_days": retention_days,
        "cutoff_at": isoformat_ms(cutoff_ms),
        "snapshots_examined": 0,
        "snapshots_deleted": 0,
        "snapshots_protected_by_reference": 0,
    }
    if retention_days <= 0:
        return report

    resolved_root = Path(root_dir).expanduser().resolve()
    archive_root = resolved_root / "archive"
    if not archive_root.is_dir():
        return report

    snapshots: List[tuple[Path, int]] = []
    for candidate in archive_root.glob("*/*/*/*"):
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        timestamp_ms = _snapshot_path_timestamp_ms(candidate, archive_root)
        if timestamp_ms is not None:
            snapshots.append((candidate, timestamp_ms))
    report["snapshots_examined"] = len(snapshots)

    retained_dirs = {
        path.resolve() for path, timestamp_ms in snapshots if timestamp_ms >= cutoff_ms
    }
    protected_dirs: set[Path] = set()
    for snapshot_dir in retained_dirs:
        manifest = _read_json(snapshot_dir / "manifest.json")
        feeds = manifest.get("feeds")
        if not isinstance(feeds, list):
            continue
        for row in feeds:
            if not isinstance(row, dict):
                continue
            referenced_dir = _referenced_snapshot_dir(
                row.get("path"),
                root_dir=resolved_root,
                archive_root=archive_root,
            )
            if referenced_dir is not None:
                protected_dirs.add(referenced_dir)

    expired_dirs = [
        path
        for path, timestamp_ms in snapshots
        if timestamp_ms < cutoff_ms
    ]
    protected_expired = {
        path.resolve() for path in expired_dirs if path.resolve() in protected_dirs
    }
    report["snapshots_protected_by_reference"] = len(protected_expired)
    for snapshot_dir in expired_dirs:
        if snapshot_dir.resolve() in protected_expired:
            continue
        shutil.rmtree(snapshot_dir)
        report["snapshots_deleted"] += 1
    return report


def _snapshot_path_timestamp_ms(
    snapshot_dir: Path, archive_root: Path
) -> Optional[int]:
    try:
        relative = snapshot_dir.relative_to(archive_root)
    except ValueError:
        return None
    if len(relative.parts) != 4:
        return None
    try:
        parsed = datetime.strptime(
            "/".join(relative.parts), "%Y/%m/%d/%H%M%SZ"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)


def _referenced_snapshot_dir(
    value: Any,
    *,
    root_dir: Path,
    archive_root: Path,
) -> Optional[Path]:
    text = str(value or "").strip()
    if not text:
        return None
    relative = Path(text)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    referenced_path = (root_dir / relative).resolve()
    try:
        archive_relative = referenced_path.relative_to(archive_root)
    except ValueError:
        return None
    if len(archive_relative.parts) < 5:
        return None
    snapshot_dir = archive_root.joinpath(*archive_relative.parts[:4])
    if _snapshot_path_timestamp_ms(snapshot_dir, archive_root) is None:
        return None
    return snapshot_dir.resolve()


if __name__ == "__main__":
    raise SystemExit(main())


MBTAArchiveConfig = TransitAgencyArchiveConfig
MBTAArchiveService = TransitAgencyArchiveService
