#!/usr/bin/env python3
"""WebSocket-based feed archiver for agencies that publish realtime via LA Metro API.

The LA Metro API streams vehicle positions and trip updates over WebSocket at:
    wss://api.metro.net/ws/{agency_id}/{endpoint}/{route_codes}

Each message is a JSON object in the format:
    {
        "id": "<vehicle_id>",
        "vehicle": { "trip": { "route_id": "...", ... }, ... },
        "route_code": "...",
        ...
    }

This module collects messages for a configurable window, then writes them into
the same snapshot directory structure used by the HTTP archiver so that the
ingest pipeline can process them identically.

Usage:
    python scripts/transit/archive_ws.py --agency lametro-rail
    python scripts/transit/archive_ws.py --agency lametro-bus --window 60
    python scripts/transit/archive_ws.py --agency lametro-rail --once
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.shared.runtime import isoformat_ms
from scripts.transit.agencies import (
    TransitAgencyAdapter,
    default_transit_agency_key,
    get_transit_agency_adapter,
)

logger = logging.getLogger("transit-archive-ws")

# Metro API base URL for HTTP (non-WS) endpoints
METRO_API_BASE = "https://api.metro.net"


# ---------------------------------------------------------------------------
# Message accumulator — collects raw WS messages into a list per feed type
# ---------------------------------------------------------------------------


@dataclass
class _FeedAccumulator:
    """Thread-safe accumulator for WebSocket messages."""

    name: str
    _lock: threading.Lock = field(
        default_factory=threading.Lock, compare=False, repr=False
    )
    _messages: List[Dict[str, Any]] = field(
        default_factory=list, compare=False, repr=False
    )
    _last_ts: float = field(default=0.0, compare=False, repr=False)

    def push(self, msg: Dict[str, Any]) -> None:
        with self._lock:
            self._messages.append(msg)
            self._last_ts = time.time()

    def drain(self) -> List[Dict[str, Any]]:
        with self._lock:
            msgs = self._messages[:]
            self._messages.clear()
            return msgs

    @property
    def last_received(self) -> float:
        with self._lock:
            return self._last_ts


# ---------------------------------------------------------------------------
# WebSocket collector thread
# ---------------------------------------------------------------------------


def _ws_collect(
    ws_url: str,
    accumulator: _FeedAccumulator,
    stop_event: threading.Event,
    reconnect_delay: float = 5.0,
) -> None:
    """Long-running WebSocket listener that pushes messages into the accumulator.

    Uses the stdlib `urllib.request` + ssl module to avoid adding a heavy
    asyncio or third-party websocket dependency.  Falls back to the
    ``websocket-client`` package if present, or emits a warning and exits.
    """
    try:
        import websocket  # type: ignore

        _ws_collect_websocket_client(
            ws_url, accumulator, stop_event, reconnect_delay, websocket
        )
        return
    except ImportError:
        pass

    # Fallback: minimal stdlib WebSocket handshake (RFC 6455)
    try:
        _ws_collect_stdlib(ws_url, accumulator, stop_event, reconnect_delay)
    except Exception:
        logger.exception("WebSocket collector failed for %s", ws_url)


def _ws_collect_websocket_client(
    ws_url: str,
    accumulator: _FeedAccumulator,
    stop_event: threading.Event,
    reconnect_delay: float,
    websocket_module: Any,
) -> None:
    """WebSocket collection using the websocket-client library."""
    while not stop_event.is_set():
        try:
            ws = websocket_module.create_connection(ws_url, timeout=30)
            logger.info("WS connected: %s", ws_url)
            while not stop_event.is_set():
                try:
                    raw = ws.recv()
                    if not raw:
                        continue
                    msg = json.loads(raw)
                    accumulator.push(msg)
                except websocket_module.WebSocketTimeoutException:
                    continue
                except Exception:
                    logger.warning("WS recv error on %s, reconnecting", ws_url)
                    break
            try:
                ws.close()
            except Exception:
                pass
        except Exception:
            logger.warning(
                "WS connect failed for %s, retrying in %.0fs", ws_url, reconnect_delay
            )
        if not stop_event.is_set():
            time.sleep(reconnect_delay)


def _ws_collect_stdlib(
    ws_url: str,
    accumulator: _FeedAccumulator,
    stop_event: threading.Event,
    reconnect_delay: float,
) -> None:
    """Minimal WebSocket collection using stdlib socket + ssl (no external deps)."""
    import base64
    import hashlib as _hashlib
    import socket
    import ssl
    import struct
    import urllib.parse

    def _open_ws(url: str):  # type: ignore
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        raw_sock = socket.create_connection((host, port), timeout=30)
        if parsed.scheme == "wss":
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(raw_sock, server_hostname=host)
        else:
            sock = raw_sock
        sock.sendall(handshake.encode())
        # Read HTTP response headers
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError(
                    "WS handshake: connection closed before headers complete"
                )
            response += chunk
        if b"101" not in response.split(b"\r\n")[0]:
            raise ConnectionError(f"WS handshake failed: {response[:200]}")
        return sock

    def _recv_frame(sock: socket.socket) -> Optional[bytes]:  # type: ignore
        try:
            header = b""
            while len(header) < 2:
                chunk = sock.recv(2 - len(header))
                if not chunk:
                    return None
                header += chunk
            fin = (header[0] & 0x80) != 0
            opcode = header[0] & 0x0F
            masked = (header[1] & 0x80) != 0
            payload_len = header[1] & 0x7F
            if payload_len == 126:
                ext = b""
                while len(ext) < 2:
                    ext += sock.recv(2 - len(ext))
                payload_len = struct.unpack("!H", ext)[0]
            elif payload_len == 127:
                ext = b""
                while len(ext) < 8:
                    ext += sock.recv(8 - len(ext))
                payload_len = struct.unpack("!Q", ext)[0]
            mask_key = b""
            if masked:
                while len(mask_key) < 4:
                    mask_key += sock.recv(4 - len(mask_key))
            payload = b""
            while len(payload) < payload_len:
                payload += sock.recv(payload_len - len(payload))
            if masked:
                payload = bytes(
                    payload[i] ^ mask_key[i % 4] for i in range(len(payload))
                )
            if opcode == 0x8:  # close
                return None
            if opcode in (0x1, 0x2, 0x0):  # text, binary, continuation
                return payload
            return b""  # ping/pong/other — ignored
        except (OSError, struct.error):
            return None

    while not stop_event.is_set():
        sock = None
        try:
            sock = _open_ws(ws_url)
            logger.info("WS connected (stdlib): %s", ws_url)
            sock.settimeout(5.0)
            while not stop_event.is_set():
                frame = _recv_frame(sock)
                if frame is None:
                    logger.warning("WS frame None, reconnecting: %s", ws_url)
                    break
                if not frame:
                    continue
                try:
                    msg = json.loads(frame.decode("utf-8"))
                    accumulator.push(msg)
                except Exception:
                    pass
        except Exception:
            logger.warning("WS stdlib error on %s", ws_url, exc_info=True)
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        if not stop_event.is_set():
            time.sleep(reconnect_delay)


# ---------------------------------------------------------------------------
# Metro-format → GTFS-RT-compatible envelope conversion
# ---------------------------------------------------------------------------


def _metro_messages_to_gtfs_rt_envelope(
    messages: List[Dict[str, Any]],
    payload_type: str,
    feed_timestamp_ms: int,
) -> Dict[str, Any]:
    """Convert a list of Metro WS messages into a GTFS-RT-style JSON envelope.

    The Metro WS sends individual vehicle/trip objects.  This wraps them in the
    standard GTFS-RT FeedMessage envelope so the existing feeds.py normalizer
    can consume them without modification.

    Metro vehicle_positions message shape:
        {
          "id": "vehicle_id",
          "vehicle": {
            "trip": { "route_id": "...", "trip_id": "...", "direction_id": 0, ... },
            "vehicle": { "id": "...", "label": "..." },
            "position": { "latitude": ..., "longitude": ..., "bearing": ..., "speed": ... },
            "timestamp": ...,
            "occupancy_status": ...,
            ...
          },
          "route_code": "..."
        }

    Metro trip_updates message shape:
        {
          "id": "vehicle_id",
          "vehicle": {
            "trip": { "route_id": "...", "trip_id": "...", ... },
            ...
          },
          ...
        }
    """
    entities = []
    seen_ids: set = set()
    for msg in reversed(messages):  # last message wins per vehicle
        entity_id = str(msg.get("id") or "")
        if not entity_id or entity_id in seen_ids:
            continue
        seen_ids.add(entity_id)
        inner = msg.get("vehicle") or msg
        if payload_type == "vehicle_positions":
            entities.append(
                {
                    "id": entity_id,
                    "vehicle": inner,
                }
            )
        elif payload_type == "trip_updates":
            trip = inner.get("trip") or {}
            entities.append(
                {
                    "id": entity_id,
                    "trip_update": {
                        "trip": trip,
                        "vehicle": inner.get("vehicle") or {},
                        "stop_time_update": inner.get("stop_time_update") or [],
                        "timestamp": inner.get("timestamp"),
                    },
                }
            )

    return {
        "header": {
            "gtfs_realtime_version": "2.0",
            "timestamp": feed_timestamp_ms // 1000,
            "feed_label": "lametro-ws",
        },
        "entity": entities,
    }


# ---------------------------------------------------------------------------
# Metro HTTP endpoints for supplementary data (canceled service as alert proxy)
# ---------------------------------------------------------------------------


def _fetch_canceled_service(
    session: requests.Session, timeout: float = 15.0
) -> Optional[Dict[str, Any]]:
    """Fetch canceled service summary from Metro API as an alert proxy.

    Returns a GTFS-RT-style alerts envelope or None if unavailable.
    """
    try:
        response = session.get(
            f"{METRO_API_BASE}/canceled_service/all", timeout=timeout
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, (list, dict)):
            return None
        items = data if isinstance(data, list) else data.get("items") or []
        if not items:
            return None
        entities = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            line = str(item.get("line") or item.get("route") or "")
            summary = str(
                item.get("summary") or item.get("description") or "Canceled service"
            )
            entities.append(
                {
                    "id": f"canceled-{i}",
                    "alert": {
                        "effect": "NO_SERVICE",
                        "cause": "OTHER_CAUSE",
                        "header_text": {
                            "translation": [{"text": summary, "language": "en"}]
                        },
                        "informed_entity": [{"route_id": line}] if line else [],
                    },
                }
            )
        return {
            "header": {
                "gtfs_realtime_version": "2.0",
                "timestamp": int(time.time()),
                "feed_label": "lametro-canceled-service",
            },
            "entity": entities,
        }
    except Exception:
        logger.debug("canceled service fetch failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Main archive service
# ---------------------------------------------------------------------------


@dataclass
class MetroWSArchiveConfig:
    agency_key: str
    system_name: str
    root_dir: Path
    api_agency_id: str
    ws_base_url: str = "wss://api.metro.net/ws"
    collect_window_seconds: float = 30.0
    static_refresh_seconds: int = 21600
    static_url: Optional[str] = None
    static_filename: str = "gtfs.zip"
    vehicle_positions_filename: str = "VehiclePositions_enhanced.json"
    trip_updates_filename: str = "TripUpdates_enhanced.json"
    alerts_filename: str = "Alerts_enhanced.json"


class MetroWSArchiveService:
    """Archive service that collects LA Metro realtime data via WebSocket."""

    def __init__(self, config: MetroWSArchiveConfig) -> None:
        self.cfg = config
        self._stop_event = threading.Event()
        self._session = requests.Session()
        self._vp_accum = _FeedAccumulator(name="vehicle_positions")
        self._tu_accum = _FeedAccumulator(name="trip_updates")
        self._threads: List[threading.Thread] = []

    def start_collectors(self) -> None:
        vp_url = (
            f"{self.cfg.ws_base_url}/{self.cfg.api_agency_id}/vehicle_positions/all"
        )
        tu_url = f"{self.cfg.ws_base_url}/{self.cfg.api_agency_id}/trip_updates/all"

        for url, accum in ((vp_url, self._vp_accum), (tu_url, self._tu_accum)):
            t = threading.Thread(
                target=_ws_collect,
                args=(url, accum, self._stop_event),
                daemon=True,
                name=f"ws-{accum.name}",
            )
            t.start()
            self._threads.append(t)
            logger.info("started WebSocket collector thread for %s", url)

    def run(self) -> None:
        logger.info(
            "Metro WS archive service starting: agency=%s root=%s",
            self.cfg.agency_key,
            self.cfg.root_dir,
        )
        self.start_collectors()
        # Give collectors a moment to connect before first snapshot
        time.sleep(min(5.0, self.cfg.collect_window_seconds * 0.2))
        while not self._stop_event.is_set():
            started = time.time()
            try:
                self.run_once()
            except Exception:
                logger.exception("WS archive iteration failed")
            elapsed = time.time() - started
            remaining = max(0.5, self.cfg.collect_window_seconds - elapsed)
            self._stop_event.wait(timeout=remaining)

    def run_once(self) -> Dict[str, Any]:
        timestamp_ms = int(time.time() * 1000)
        snapshot_dir = self._snapshot_dir(timestamp_ms)
        current_dir = self.cfg.root_dir / "current"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        current_dir.mkdir(parents=True, exist_ok=True)

        manifest_feeds: List[Dict[str, Any]] = []

        # --- Static GTFS (refresh on interval) ---
        if self.cfg.static_url:
            static_path = current_dir / self.cfg.static_filename
            if self._should_refresh_static(static_path, timestamp_ms):
                try:
                    resp = self._session.get(self.cfg.static_url, timeout=60)
                    resp.raise_for_status()
                    _atomic_write(static_path, resp.content, binary=True)
                    snap_static = snapshot_dir / self.cfg.static_filename
                    _atomic_write(snap_static, resp.content, binary=True)
                    meta = {
                        "name": "static_gtfs",
                        "url": self.cfg.static_url,
                        "filename": self.cfg.static_filename,
                        "captured_at": isoformat_ms(timestamp_ms),
                        "timestamp_ms": timestamp_ms,
                        "sha256": hashlib.sha256(resp.content).hexdigest(),
                        "content_length": len(resp.content),
                        "status": "archived",
                        "path": str(snap_static.relative_to(self.cfg.root_dir)),
                    }
                    _write_json(
                        snapshot_dir / f"{self.cfg.static_filename}.meta.json", meta
                    )
                    manifest_feeds.append(meta)
                    logger.info("archived static GTFS for %s", self.cfg.agency_key)
                except Exception:
                    logger.warning(
                        "static GTFS fetch failed for %s",
                        self.cfg.agency_key,
                        exc_info=True,
                    )

        # --- Vehicle positions from WS accumulator ---
        vp_messages = self._vp_accum.drain()
        if vp_messages:
            vp_envelope = _metro_messages_to_gtfs_rt_envelope(
                vp_messages, "vehicle_positions", timestamp_ms
            )
            vp_content = json.dumps(vp_envelope, indent=2).encode("utf-8")
            _atomic_write(
                current_dir / self.cfg.vehicle_positions_filename,
                vp_content,
                binary=False,
            )
            snap_vp = snapshot_dir / self.cfg.vehicle_positions_filename
            _atomic_write(snap_vp, vp_content, binary=False)
            meta = _feed_meta(
                "vehicle_positions",
                f"ws://{self.cfg.api_agency_id}/vehicle_positions",
                self.cfg.vehicle_positions_filename,
                vp_content,
                timestamp_ms,
                self.cfg.root_dir,
                snap_vp,
                vehicle_count=len(vp_envelope["entity"]),
            )
            _write_json(
                snapshot_dir / f"{self.cfg.vehicle_positions_filename}.meta.json", meta
            )
            manifest_feeds.append(meta)
            logger.info(
                "archived %d vehicle position records for %s",
                len(vp_envelope["entity"]),
                self.cfg.agency_key,
            )

        # --- Trip updates from WS accumulator ---
        tu_messages = self._tu_accum.drain()
        if tu_messages:
            tu_envelope = _metro_messages_to_gtfs_rt_envelope(
                tu_messages, "trip_updates", timestamp_ms
            )
            tu_content = json.dumps(tu_envelope, indent=2).encode("utf-8")
            _atomic_write(
                current_dir / self.cfg.trip_updates_filename, tu_content, binary=False
            )
            snap_tu = snapshot_dir / self.cfg.trip_updates_filename
            _atomic_write(snap_tu, tu_content, binary=False)
            meta = _feed_meta(
                "trip_updates",
                f"ws://{self.cfg.api_agency_id}/trip_updates",
                self.cfg.trip_updates_filename,
                tu_content,
                timestamp_ms,
                self.cfg.root_dir,
                snap_tu,
                vehicle_count=len(tu_envelope["entity"]),
            )
            _write_json(
                snapshot_dir / f"{self.cfg.trip_updates_filename}.meta.json", meta
            )
            manifest_feeds.append(meta)

        # --- Alerts: use canceled service as proxy (no public alert feed) ---
        alerts_envelope = _fetch_canceled_service(self._session)
        if alerts_envelope is not None:
            alerts_content = json.dumps(alerts_envelope, indent=2).encode("utf-8")
            _atomic_write(
                current_dir / self.cfg.alerts_filename, alerts_content, binary=False
            )
            snap_alerts = snapshot_dir / self.cfg.alerts_filename
            _atomic_write(snap_alerts, alerts_content, binary=False)
            meta = _feed_meta(
                "alerts",
                f"{METRO_API_BASE}/canceled_service/all",
                self.cfg.alerts_filename,
                alerts_content,
                timestamp_ms,
                self.cfg.root_dir,
                snap_alerts,
            )
            _write_json(snapshot_dir / f"{self.cfg.alerts_filename}.meta.json", meta)
            manifest_feeds.append(meta)
        else:
            # Write an empty alerts envelope so the ingest pipeline doesn't
            # fail trying to load a missing alerts file.
            empty_alerts: Dict[str, Any] = {
                "header": {
                    "gtfs_realtime_version": "2.0",
                    "timestamp": timestamp_ms // 1000,
                    "feed_label": "lametro-no-alerts",
                },
                "entity": [],
            }
            empty_content = json.dumps(empty_alerts, indent=2).encode("utf-8")
            _atomic_write(
                current_dir / self.cfg.alerts_filename, empty_content, binary=False
            )
            snap_alerts = snapshot_dir / self.cfg.alerts_filename
            _atomic_write(snap_alerts, empty_content, binary=False)
            meta = {
                "name": "alerts",
                "url": None,
                "filename": self.cfg.alerts_filename,
                "captured_at": isoformat_ms(timestamp_ms),
                "timestamp_ms": timestamp_ms,
                "status": "no_public_feed",
                "note": "No documented public LA Metro alert endpoint; empty envelope written.",
                "path": str(snap_alerts.relative_to(self.cfg.root_dir)),
            }
            manifest_feeds.append(meta)

        manifest = {
            "agency": self.cfg.system_name,
            "agency_key": self.cfg.agency_key,
            "captured_at": isoformat_ms(timestamp_ms),
            "timestamp_ms": timestamp_ms,
            "snapshot_path": str(snapshot_dir.relative_to(self.cfg.root_dir)),
            "ingest_mode": "websocket",
            "feeds": manifest_feeds,
        }
        _write_json(snapshot_dir / "manifest.json", manifest)
        _write_json(current_dir / "manifest.json", manifest)
        return manifest

    def stop(self) -> None:
        self._stop_event.set()

    def _should_refresh_static(self, current_path: Path, timestamp_ms: int) -> bool:
        if not current_path.exists():
            return True
        age_s = max(
            0.0, (timestamp_ms - int(current_path.stat().st_mtime * 1000)) / 1000.0
        )
        return age_s >= max(60, self.cfg.static_refresh_seconds)

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _feed_meta(
    name: str,
    url: str,
    filename: str,
    content: bytes,
    timestamp_ms: int,
    root_dir: Path,
    snapshot_path: Path,
    vehicle_count: Optional[int] = None,
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "name": name,
        "url": url,
        "filename": filename,
        "captured_at": isoformat_ms(timestamp_ms),
        "timestamp_ms": timestamp_ms,
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_length": len(content),
        "status": "archived",
        "path": str(snapshot_path.relative_to(root_dir)),
    }
    if vehicle_count is not None:
        meta["entity_count"] = vehicle_count
    return meta


def _atomic_write(path: Path, content: bytes, *, binary: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if binary:
        tmp.write_bytes(content)
    else:
        tmp.write_text(content.decode("utf-8"), encoding="utf-8")
    tmp.replace(path)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Archive LA Metro realtime feeds via WebSocket"
    )
    p.add_argument(
        "--agency",
        default=os.getenv("TRANSIT_AGENCY", "lametro-rail"),
        help="Agency adapter key (lametro-rail or lametro-bus)",
    )
    p.add_argument(
        "--root-dir",
        default=None,
        help="Override archive root directory",
    )
    p.add_argument(
        "--window",
        type=float,
        default=float(os.getenv("TRANSIT_WS_WINDOW_SECONDS", "30")),
        help="Seconds to collect WS messages before writing a snapshot (default: 30)",
    )
    p.add_argument(
        "--static-refresh-seconds",
        type=int,
        default=int(os.getenv("TRANSIT_STATIC_REFRESH_SECONDS", "21600")),
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="Run a single collection window then exit (useful for testing)",
    )
    return p


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    args = build_parser().parse_args()
    adapter: TransitAgencyAdapter = get_transit_agency_adapter(args.agency)

    if not adapter.realtime_via_websocket:
        logger.error(
            "Agency %s is not configured for WebSocket ingest; "
            "use archive.py for HTTP-based agencies.",
            adapter.key,
        )
        return 1

    root_dir = (
        Path(args.root_dir).resolve()
        if args.root_dir
        else adapter.archive_root_path().resolve()
    )
    cfg = MetroWSArchiveConfig(
        agency_key=adapter.key,
        system_name=adapter.system_name,
        root_dir=root_dir,
        api_agency_id=adapter.api_agency_id or adapter.key,
        ws_base_url=adapter.websocket_base_url or "wss://api.metro.net/ws",
        collect_window_seconds=max(5.0, args.window),
        static_refresh_seconds=max(60, args.static_refresh_seconds),
        static_url=adapter.static_url,
        static_filename=adapter.static_feed_filename,
        vehicle_positions_filename=adapter.vehicle_positions_filename,
        trip_updates_filename=adapter.trip_updates_filename,
        alerts_filename=adapter.alerts_filename,
    )
    service = MetroWSArchiveService(cfg)

    def _handle_signal(signum: int, _frame: Any) -> None:
        logger.info("received signal %s, stopping", signum)
        service.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.once:
        # Start collectors, wait one window, snapshot, exit
        service.start_collectors()
        time.sleep(min(args.window, 10.0))
        service.run_once()
        service.stop()
        return 0

    service.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
