#!/usr/bin/env python3
"""Capture self-contained LA Metro bus and rail discovery snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.shared.runtime import isoformat_ms
from scripts.transit.feeds import validate_gtfs_realtime_payload

LOG = logging.getLogger("lametro-capture")


@dataclass(frozen=True)
class Source:
    name: str
    filename: str
    url: str
    mode: str
    static: bool = False


SOURCES = (
    Source(
        "bus_static_gtfs", "bus_gtfs.zip", "https://gitlab.com/LACMTA/gtfs_bus/raw/master/gtfs_bus.zip", "bus", True
    ),
    Source(
        "bus_vehicle_positions",
        "bus_vehicle_positions.pb",
        "https://api.goswift.ly/real-time/lametro/gtfs-rt-vehicle-positions",
        "bus",
    ),
    Source(
        "bus_trip_updates",
        "bus_trip_updates.pb",
        "https://api.goswift.ly/real-time/lametro/gtfs-rt-trip-updates",
        "bus",
    ),
    Source("bus_alerts", "bus_alerts.pb", "https://api.goswift.ly/real-time/lametro/gtfs-rt-alerts", "bus"),
    Source(
        "rail_static_gtfs",
        "rail_gtfs.zip",
        "https://gitlab.com/LACMTA/gtfs_rail/raw/master/gtfs_rail.zip",
        "rail",
        True,
    ),
    Source(
        "rail_vehicle_positions",
        "rail_vehicle_positions.pb",
        "https://api.goswift.ly/real-time/lametro-rail/gtfs-rt-vehicle-positions",
        "rail",
    ),
    Source(
        "rail_trip_updates",
        "rail_trip_updates.pb",
        "https://api.goswift.ly/real-time/lametro-rail/gtfs-rt-trip-updates",
        "rail",
    ),
    Source("rail_alerts", "rail_alerts.pb", "https://api.goswift.ly/real-time/lametro-rail/gtfs-rt-alerts", "rail"),
)


class Capture:
    def __init__(self, root: Path, timeout: float, static_refresh: int, min_free_gb: float) -> None:
        self.root = root.resolve()
        self.timeout = timeout
        self.static_refresh = static_refresh
        self.min_free_bytes = int(min_free_gb * 1024**3)
        self.session = requests.Session()
        self.stopped = False
        self.stop_event = threading.Event()

    def run_once(self) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(self.root).free
        if free < self.min_free_bytes:
            raise RuntimeError(
                f"capture disk guard: {free / 1024**3:.2f} GiB free; requires {self.min_free_bytes / 1024**3:.2f} GiB"
            )
        timestamp_ms = int(time.time() * 1000)
        stamp = time.gmtime(timestamp_ms / 1000)
        relative = Path("archive") / time.strftime("%Y/%m/%d/%H%M%SZ", stamp)
        snapshot = self.root / relative
        current = self.root / "current"
        snapshot.mkdir(parents=True)
        current.mkdir(parents=True, exist_ok=True)
        rows: list[dict[str, Any]] = []
        for source in SOURCES:
            destination = current / source.filename
            try:
                if (
                    source.static
                    and destination.exists()
                    and time.time() - destination.stat().st_mtime < self.static_refresh
                ):
                    content = destination.read_bytes()
                    status = "reused_current"
                else:
                    content, response_headers = self._fetch(source)
                    self._validate(source, content, response_headers.get("content-type"))
                    _atomic_write(destination, content)
                    status = "captured"
                if source.static:
                    digest = hashlib.sha256(content).hexdigest()
                    archived_path = Path("anchors") / f"{source.mode}_gtfs_{digest[:16]}.zip"
                    anchor = self.root / archived_path
                    if not anchor.exists():
                        _atomic_write(anchor, content)
                else:
                    archived_path = relative / source.filename
                    _atomic_write(self.root / archived_path, content)
                rows.append(self._row(source, timestamp_ms, status, content, archived_path))
            except Exception as exc:
                LOG.warning("%s failed: %s", source.name, exc)
                rows.append(
                    {
                        "name": source.name,
                        "mode": source.mode,
                        "url": source.url,
                        "filename": source.filename,
                        "captured_at": isoformat_ms(timestamp_ms),
                        "timestamp_ms": timestamp_ms,
                        "status": "failed",
                        "error": str(exc),
                        "path": None,
                    }
                )
        manifest = {
            "agency": "LA Metro",
            "agency_key": "lametro",
            "capture_schema": 1,
            "captured_at": isoformat_ms(timestamp_ms),
            "timestamp_ms": timestamp_ms,
            "snapshot_path": str(relative),
            "history_enabled": True,
            "feeds": rows,
            "mode_status": {
                mode: "available"
                if any(
                    row["mode"] == mode and row["status"] != "failed" and not row["name"].endswith("static_gtfs")
                    for row in rows
                )
                else "unavailable"
                for mode in ("bus", "rail")
            },
        }
        _write_json(snapshot / "manifest.json", manifest)
        _write_json(current / "manifest.json", manifest)
        return manifest

    def _fetch(self, source: Source) -> tuple[bytes, dict[str, str]]:
        headers = {}
        if "goswift.ly" in source.url:
            key = os.getenv("SWIFTLY_API_KEY", "").strip()
            if not key:
                raise RuntimeError("SWIFTLY_API_KEY is not configured")
            headers["Authorization"] = key
        response = self.session.get(source.url, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        return response.content, {key.lower(): value for key, value in response.headers.items()}

    @staticmethod
    def _validate(source: Source, content: bytes, content_type: str | None) -> None:
        if source.static:
            if not content.startswith(b"PK\x03\x04"):
                raise ValueError("static feed is not a zip archive")
        else:
            validate_gtfs_realtime_payload(content, content_type=content_type)

    @staticmethod
    def _row(source: Source, timestamp_ms: int, status: str, content: bytes, path: Path) -> dict[str, Any]:
        return {
            "name": source.name,
            "mode": source.mode,
            "url": source.url,
            "filename": source.filename,
            "captured_at": isoformat_ms(timestamp_ms),
            "timestamp_ms": timestamp_ms,
            "status": status,
            "sha256": hashlib.sha256(content).hexdigest(),
            "content_length": len(content),
            "path": str(path),
        }


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_write(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-dir", default="data/feeds/lametro")
    parser.add_argument("--interval", type=float, default=60)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--static-refresh-seconds", type=int, default=21600)
    parser.add_argument("--min-free-gb", type=float, default=3)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s :: %(message)s"
    )
    capture = Capture(
        Path(args.root_dir), max(1, args.timeout), max(60, args.static_refresh_seconds), max(0, args.min_free_gb)
    )

    def stop(*_: Any) -> None:
        capture.stopped = True
        capture.stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while not capture.stopped:
        started = time.monotonic()
        try:
            capture.run_once()
        except RuntimeError as exc:
            if str(exc).startswith("capture disk guard:"):
                LOG.error("%s; stopping collector", exc)
                break
            LOG.exception("capture iteration failed")
        except Exception:
            LOG.exception("capture iteration failed")
        if args.once:
            break
        capture.stop_event.wait(max(0.2, args.interval - (time.monotonic() - started)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
