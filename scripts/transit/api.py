#!/usr/bin/env python3
"""HTTP API for the Transit Sentinel dashboard."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.shared.runtime import isoformat_ms
from scripts.transit.agencies import (
    default_transit_agency_key,
    get_transit_agency_adapter,
)
from scripts.transit.store import TransitStore

logger = logging.getLogger("transit-api")


class TransitAPIService:
    def __init__(self, redis_url: str, *, system_name: str = "MBTA") -> None:
        self.system_name = system_name
        self.store = TransitStore(redis_url)

    def service_health(self) -> Dict[str, Any]:
        ingest_status = self.store.read_status("ops:transit_ingest_status")
        latest_health = self.store.health()
        status = str(
            ingest_status.get("status") or latest_health.get("status") or "idle"
        )
        return {
            "service": "Transit Sentinel API",
            "timestamp": isoformat_ms(),
            "system_name": str(latest_health.get("system_name") or self.system_name),
            "ingest_status": ingest_status,
            "status": status,
        }

    def transit_health(
        self, *, scope: str = "all", trace_id: str | None = None
    ) -> Dict[str, Any]:
        return self.store.health(scope=scope, trace_id=trace_id)

    def transit_entities(
        self, *, scope: str = "all", trace_id: str | None = None
    ) -> Dict[str, Any]:
        return self.store.entities(scope=scope, trace_id=trace_id)

    def transit_regimes(
        self, *, scope: str = "all", trace_id: str | None = None
    ) -> Dict[str, Any]:
        return self.store.regimes(scope=scope, trace_id=trace_id)

    def transit_incidents(
        self, *, scope: str = "all", trace_id: str | None = None
    ) -> Dict[str, Any]:
        return self.store.incidents(scope=scope, trace_id=trace_id)

    def transit_trends(
        self, *, scope: str = "all", trace_id: str | None = None
    ) -> Dict[str, Any]:
        return self.store.trends(scope=scope, trace_id=trace_id)

    def transit_history(
        self,
        *,
        entity_id: str,
        scope: str = "all",
        trace_id: str | None = None,
        limit: int = 72,
    ) -> Dict[str, Any]:
        return self.store.history(
            entity_id, scope=scope, trace_id=trace_id, limit=limit
        )

    def transit_sources(self) -> Dict[str, Any]:
        return self.store.sources()

    def transit_scorecard(
        self,
        *,
        scope: str = "all",
        trace_id: str | None = None,
        limit: int = 720,
    ) -> Dict[str, Any]:
        """Rolling KPI scorecard for the operations dashboard and contract reporting."""
        return self.store.scorecard(scope=scope, trace_id=trace_id, limit=limit)

    def transit_map(
        self, *, scope: str = "all", trace_id: str | None = None
    ) -> Dict[str, Any]:
        """Return a GeoJSON-compatible map payload for the dashboard map view.

        Combines:
        - vehicle positions (lat/lon) tagged with corridor regime and hazard
        - corridor regime summary keyed by entity_id
        - active incidents keyed by entity_id
        """
        entities = self.store.entities(scope=scope, trace_id=trace_id)
        regimes_payload = self.store.regimes(scope=scope, trace_id=trace_id)
        incidents_payload = self.store.incidents(scope=scope, trace_id=trace_id)

        # Index regimes and incidents by entity_id for O(1) lookup
        regime_by_entity: Dict[str, Any] = {
            str(r.get("entity_id") or ""): r
            for r in (regimes_payload.get("regimes") or [])
            if isinstance(r, dict) and r.get("entity_id")
        }
        incidents_by_entity: Dict[str, list] = {}
        for inc in incidents_payload.get("incidents") or []:
            if not isinstance(inc, dict):
                continue
            eid = str(inc.get("entity_id") or "")
            incidents_by_entity.setdefault(eid, []).append(inc)

        # Build vehicle features (GeoJSON Point)
        vehicle_features = []
        for v in entities.get("vehicles") or []:
            if not isinstance(v, dict):
                continue
            obs = v.get("observation") or v
            lat = obs.get("latitude") if isinstance(obs, dict) else None
            lon = obs.get("longitude") if isinstance(obs, dict) else None
            if lat is None or lon is None:
                continue
            try:
                lat = float(lat)
                lon = float(lon)
            except (TypeError, ValueError):
                continue
            vehicle_id = str(obs.get("vehicle_id") or v.get("entity_id") or "")
            route_id = str(obs.get("route_id") or "")
            corridor_key = f"{route_id}:0" if route_id else ""
            regime_rec = regime_by_entity.get(corridor_key) or {}
            vehicle_features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "entity_id": v.get("entity_id"),
                        "vehicle_id": vehicle_id,
                        "route_id": route_id,
                        "direction_id": obs.get("direction_id")
                        if isinstance(obs, dict)
                        else None,
                        "delay_seconds": obs.get("delay_seconds")
                        if isinstance(obs, dict)
                        else None,
                        "current_status": obs.get("current_status")
                        if isinstance(obs, dict)
                        else None,
                        "occupancy_status": obs.get("occupancy_status")
                        if isinstance(obs, dict)
                        else None,
                        "bearing": obs.get("bearing")
                        if isinstance(obs, dict)
                        else None,
                        "hazard_score": regime_rec.get("hazard_score"),
                        "regime": regime_rec.get("regime"),
                        "label": v.get("label") or v.get("route_label") or route_id,
                        "timestamp_ms": obs.get("timestamp_ms")
                        if isinstance(obs, dict)
                        else None,
                    },
                }
            )

        # Build corridor features (summarised, no geometry — shape data lives in GTFS)
        corridor_features = []
        for line in (entities.get("active_lines") or []) + (
            entities.get("lines") or []
        ):
            if not isinstance(line, dict):
                continue
            eid = str(line.get("entity_id") or "")
            regime_rec = regime_by_entity.get(eid) or {}
            active_incidents = incidents_by_entity.get(eid) or []
            corridor_features.append(
                {
                    "entity_id": eid,
                    "route_id": line.get("route_id"),
                    "direction_id": line.get("direction_id"),
                    "label": line.get("label") or line.get("route_id"),
                    "regime": regime_rec.get("regime") or line.get("regime"),
                    "hazard_score": regime_rec.get("hazard_score")
                    or line.get("hazard_score"),
                    "active_vehicles": line.get("active_vehicle_count"),
                    "incident_count": len(active_incidents),
                    "top_action": regime_rec.get("action"),
                    "timestamp_ms": regime_rec.get("timestamp_ms")
                    or line.get("timestamp_ms"),
                }
            )

        return {
            "type": "FeatureCollection",
            "scope": scope,
            "trace_id": trace_id,
            "timestamp": isoformat_ms(),
            "vehicle_features": vehicle_features,
            "corridor_summaries": corridor_features,
            "vehicle_count": len(vehicle_features),
            "corridor_count": len(corridor_features),
        }


class TransitAPIHandler(BaseHTTPRequestHandler):
    server_version = "TransitSentinel/1.0"

    @property
    def svc(self) -> TransitAPIService:  # type: ignore[override]
        return getattr(self.server, "transit_service")

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # pragma: no cover - exercised via integration tests
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query or "")
        scope = (params.get("scope") or ["all"])[0]
        trace_id = (params.get("trace_id") or [""])[0] or None

        if parsed.path in {"/health", "/api/health", "/api/status"}:
            self._send_json(self.svc.service_health())
            return
        if parsed.path == "/api/transit/health":
            self._send_json(self.svc.transit_health(scope=scope, trace_id=trace_id))
            return
        if parsed.path == "/api/transit/entities":
            self._send_json(self.svc.transit_entities(scope=scope, trace_id=trace_id))
            return
        if parsed.path == "/api/transit/regimes":
            self._send_json(self.svc.transit_regimes(scope=scope, trace_id=trace_id))
            return
        if parsed.path == "/api/transit/incidents":
            self._send_json(self.svc.transit_incidents(scope=scope, trace_id=trace_id))
            return
        if parsed.path == "/api/transit/trends":
            self._send_json(self.svc.transit_trends(scope=scope, trace_id=trace_id))
            return
        if parsed.path == "/api/transit/history":
            entity_id = (params.get("entity_id") or [""])[0]
            if not entity_id.strip():
                self._send_json({"error": "missing_entity_id"}, status=400)
                return
            limit_raw = (params.get("limit") or ["72"])[0]
            try:
                limit = int(limit_raw)
            except ValueError:
                limit = 72
            self._send_json(
                self.svc.transit_history(
                    entity_id=entity_id,
                    scope=scope,
                    trace_id=trace_id,
                    limit=max(1, min(limit, 1000)),
                )
            )
            return
        if parsed.path == "/api/transit/sources":
            self._send_json(self.svc.transit_sources())
            return
        if parsed.path == "/api/transit/map":
            self._send_json(self.svc.transit_map(scope=scope, trace_id=trace_id))
            return
        if parsed.path == "/api/transit/scorecard":
            limit_raw = (params.get("limit") or ["720"])[0]
            try:
                sc_limit = int(limit_raw)
            except ValueError:
                sc_limit = 720
            self._send_json(
                self.svc.transit_scorecard(
                    scope=scope,
                    trace_id=trace_id,
                    limit=max(1, min(sc_limit, 2000)),
                )
            )
            return
        self._send_json({"error": "not_found"}, status=404)

    def do_POST(self) -> None:  # pragma: no cover - no mutating endpoints yet
        self._send_json({"error": "not_found"}, status=404)

    def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover
        logger.info("%s - %s", self.address_string(), format % args)


def start_transit_http_server(
    service: TransitAPIService, host: str = "0.0.0.0", port: int = 8000
) -> HTTPServer:
    class _Server(HTTPServer):  # pragma: no cover
        def __init__(self, address):
            super().__init__(address, TransitAPIHandler)
            self.transit_service = service

    server = _Server((host, port))

    def _serve() -> None:
        logger.info("Transit API listening on %s:%s", host, port)
        server.serve_forever()

    thread = threading.Thread(target=_serve, name="TransitAPI", daemon=True)
    thread.start()
    return server


def build_parser() -> argparse.ArgumentParser:
    adapter = get_transit_agency_adapter(
        os.getenv("TRANSIT_AGENCY", default_transit_agency_key())
    )
    parser = argparse.ArgumentParser(description="Run the Transit Sentinel API server")
    parser.add_argument(
        "--redis", default=os.getenv("VALKEY_URL", "redis://localhost:6379/0")
    )
    parser.add_argument("--host", default=os.getenv("TRANSIT_API_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("TRANSIT_API_PORT", "8000"))
    )
    parser.add_argument(
        "--system-name", default=os.getenv("TRANSIT_SYSTEM_NAME", adapter.system_name)
    )
    return parser


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    args = build_parser().parse_args()
    service = TransitAPIService(str(args.redis), system_name=str(args.system_name))
    server = start_transit_http_server(
        service, host=str(args.host), port=int(args.port)
    )

    stop = False

    def _handle_signal(signum: int, _frame: Any) -> None:
        nonlocal stop
        logger.info("received signal %s, stopping transit api", signum)
        stop = True
        server.shutdown()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    while not stop:
        time.sleep(0.5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
