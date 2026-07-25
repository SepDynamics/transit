#!/usr/bin/env python3
"""HTTP API for the Transit Sentinel dashboard."""

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
from collections import OrderedDict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict
from urllib.parse import parse_qs, urlparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.shared.runtime import isoformat_ms
from scripts.transit.agencies import (
    default_transit_agency_key,
    get_transit_agency_adapter,
)
from scripts.transit.auth import (
    ROLE_OPERATOR,
    ROLE_ADMIN,
    ROLE_VIEWER,
    check_auth,
    read_audit_trail,
    write_audit_event,
)
from scripts.transit.severity import (
    SEVERITY_LABELS,
    SEVERITY_COLOR,
    build_route_status,
    classify_network_severity,
    severity_rank,
)
from scripts.transit.store import TransitStore

logger = logging.getLogger("transit-api")


class TransitAPIService:
    def __init__(
        self,
        redis_url: str,
        *,
        system_name: str = "MBTA",
        store: TransitStore | None = None,
    ) -> None:
        self.system_name = system_name
        self.store = store or TransitStore(redis_url)
        self._cache_ttl = _float_env("TRANSIT_API_CACHE_TTL_SECONDS", 5.0)
        self._cache_max_entries = _int_env("TRANSIT_API_CACHE_MAX_ENTRIES", 32)
        self._scorecard_cache_ttl = _float_env(
            "TRANSIT_API_SCORECARD_CACHE_TTL_SECONDS", 60.0
        )
        self._scorecard_max_limit = _int_env("TRANSIT_API_SCORECARD_MAX_LIMIT", 2000)
        self._scorecard_cache_max_limit = _int_env(
            "TRANSIT_API_SCORECARD_CACHE_MAX_LIMIT", 240
        )
        self._cache: OrderedDict[
            tuple[Any, ...], tuple[float, Dict[str, Any]]
        ] = OrderedDict()
        self._cache_lock = threading.RLock()

    def _cached_payload(
        self,
        key: tuple[Any, ...],
        builder: Callable[[], Dict[str, Any]],
        *,
        ttl: float | None = None,
    ) -> Dict[str, Any]:
        now = time.monotonic()
        cache_ttl = self._cache_ttl if ttl is None else float(ttl)
        if cache_ttl > 0:
            with self._cache_lock:
                cached = self._cache.get(key)
                if cached and cached[0] >= now:
                    self._cache.move_to_end(key)
                    return cached[1]
        payload = builder()
        if cache_ttl > 0 and self._cache_max_entries > 0:
            with self._cache_lock:
                self._cache[key] = (now + cache_ttl, payload)
                self._cache.move_to_end(key)
                while len(self._cache) > self._cache_max_entries:
                    self._cache.popitem(last=False)
        return payload

    def _live_read_model(
        self,
        kind: str,
        *,
        scope: str,
        trace_id: str | None,
        expected: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        if scope != "live" or trace_id not in (None, ""):
            return {}
        reader = getattr(self.store, "read_live_read_model", None)
        if not callable(reader):
            return {}
        payload = reader(kind)
        if not isinstance(payload, dict) or not payload:
            return {}
        metadata = payload.get("read_model")
        if not isinstance(metadata, dict):
            return {}
        if metadata.get("kind") != kind or metadata.get("scope") != "live":
            return {}
        for key, expected_value in (expected or {}).items():
            if str(metadata.get(key)) != str(expected_value):
                return {}
        return payload

    def service_health(self) -> Dict[str, Any]:
        return self._cached_payload(("service_health",), self._service_health_uncached)

    def _service_health_uncached(self) -> Dict[str, Any]:
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

    # ---------------------------------------------------------------------------
    # Incident acknowledgement
    # ---------------------------------------------------------------------------

    def acknowledge_incident(
        self,
        incident_id: str,
        *,
        note: str = "",
        acknowledged_by: str | None = None,
    ) -> Dict[str, Any]:
        """Mark an incident as acknowledged.

        Persists an acknowledgement record to Valkey and returns the updated
        acknowledgement payload.
        """
        if not incident_id or not incident_id.strip():
            return {"error": "missing_incident_id"}
        incident_id = incident_id.strip()
        ack_key = f"transit:ack:{incident_id}"
        existing = self.store.read_json_key(ack_key, default={})
        if existing:
            # Already acknowledged — return current state (idempotent)
            return {"acknowledged": True, "incident_id": incident_id, **existing}
        ack_payload = {
            "incident_id": incident_id,
            "acknowledged_at": isoformat_ms(),
            "acknowledged_by": str(acknowledged_by or ""),
            "note": str(note or ""),
        }
        self.store.write_status(ack_key, ack_payload)
        return {"acknowledged": True, **ack_payload}

    def get_acknowledgement(self, incident_id: str) -> Dict[str, Any]:
        """Return the acknowledgement record for *incident_id*, or empty."""
        if not incident_id or not incident_id.strip():
            return {}
        ack_key = f"transit:ack:{incident_id.strip()}"
        return self.store.read_json_key(ack_key, default={})

    def record_incident_feedback(
        self,
        incident_id: str,
        *,
        disposition: str,
        cause: str = "",
        action_taken: str = "",
        note: str = "",
        recorded_by: str | None = None,
    ) -> Dict[str, Any]:
        """Record an operator outcome without changing the advisory score."""
        if not incident_id or not incident_id.strip():
            return {"error": "missing_incident_id"}
        allowed = {"acknowledged", "dismissed", "action_taken", "false_positive"}
        if disposition not in allowed:
            return {"error": "invalid_disposition", "allowed": sorted(allowed)}
        payload = {
            "incident_id": incident_id.strip(),
            "disposition": disposition,
            "cause": str(cause or "").strip(),
            "action_taken": str(action_taken or "").strip(),
            "note": str(note or "").strip(),
            "recorded_at": isoformat_ms(),
            "recorded_by": str(recorded_by or ""),
        }
        key = f"transit:feedback:{payload['incident_id']}"
        existing = self.store.read_json_key(key, default={})
        entries = list(existing.get("entries") or []) if isinstance(existing, dict) else []
        entries.append(payload)
        result = {"incident_id": payload["incident_id"], "entries": entries[-100:]}
        self.store.write_status(key, result)
        return result

    # ---------------------------------------------------------------------------
    # Audit trail
    # ---------------------------------------------------------------------------

    def audit_trail(self, *, limit: int = 100) -> Dict[str, Any]:
        """Return the most recent audit events."""
        events = read_audit_trail(self.store.client, limit=max(1, min(limit, 500)))
        return {
            "generated_at": isoformat_ms(),
            "event_count": len(events),
            "events": events,
        }

    def transit_health(
        self, *, scope: str = "all", trace_id: str | None = None
    ) -> Dict[str, Any]:
        return self._cached_payload(
            ("health", scope, trace_id),
            lambda: self.store.health(scope=scope, trace_id=trace_id),
        )

    def transit_entities(
        self, *, scope: str = "all", trace_id: str | None = None
    ) -> Dict[str, Any]:
        # Full entity payloads include route geometry and vehicle state. They are
        # intentionally not retained in the API cache on small live hosts.
        return self.store.entities(scope=scope, trace_id=trace_id)

    def transit_regimes(
        self, *, scope: str = "all", trace_id: str | None = None
    ) -> Dict[str, Any]:
        return self._cached_payload(
            ("regimes", scope, trace_id),
            lambda: self.store.regimes(scope=scope, trace_id=trace_id),
        )

    def transit_incidents(
        self, *, scope: str = "all", trace_id: str | None = None
    ) -> Dict[str, Any]:
        return self._cached_payload(
            ("incidents", scope, trace_id),
            lambda: self.store.incidents(scope=scope, trace_id=trace_id),
        )

    def transit_trends(
        self, *, scope: str = "all", trace_id: str | None = None
    ) -> Dict[str, Any]:
        read_model = self._live_read_model(
            "trends",
            scope=scope,
            trace_id=trace_id,
            expected={"limit": 6, "window": 24},
        )
        if read_model:
            return read_model
        return self._cached_payload(
            ("trends", scope, trace_id),
            lambda: self.store.trends(scope=scope, trace_id=trace_id),
        )

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
        return self._cached_payload(("sources",), self.store.sources)

    def transit_scorecard(
        self,
        *,
        scope: str = "all",
        trace_id: str | None = None,
        limit: int = 720,
    ) -> Dict[str, Any]:
        """Rolling KPI scorecard for the operations dashboard and contract reporting."""
        limit = max(1, min(int(limit), max(1, self._scorecard_max_limit)))
        read_model = self._live_read_model(
            "scorecard",
            scope=scope,
            trace_id=trace_id,
            expected={"limit": limit},
        )
        if read_model:
            return read_model
        if limit <= max(0, self._scorecard_cache_max_limit):
            return self._cached_payload(
                ("scorecard", scope, trace_id, int(limit)),
                lambda: self.store.scorecard(
                    scope=scope, trace_id=trace_id, limit=limit
                ),
                ttl=self._scorecard_cache_ttl,
            )
        return self.store.scorecard(scope=scope, trace_id=trace_id, limit=limit)

    def transit_dashboard(
        self, *, scope: str = "all", trace_id: str | None = None
    ) -> Dict[str, Any]:
        """One-shot operations dashboard payload for the browser console."""
        read_model = self._live_read_model("dashboard", scope=scope, trace_id=trace_id)
        if read_model:
            return read_model

        def _build() -> Dict[str, Any]:
            health = self.store.health(scope=scope, trace_id=trace_id)
            return {
                "generated_at": isoformat_ms(),
                "scope": scope,
                "trace_id": health.get("trace_id") or trace_id,
                "health": health,
                "entities": _dashboard_entities_payload(
                    self.store.entities(scope=scope, trace_id=trace_id)
                ),
                "regimes": self.store.regimes(scope=scope, trace_id=trace_id),
                "incidents": self.store.incidents(scope=scope, trace_id=trace_id),
                "trends": self.store.trends(scope=scope, trace_id=trace_id),
            }

        return self._cached_payload(("dashboard", scope, trace_id), _build)

    def public_status_routes(
        self, *, scope: str = "live", trace_id: str | None = None
    ) -> Dict[str, Any]:
        return self._cached_payload(
            ("public_status_routes", scope, trace_id),
            lambda: self._public_status_routes_uncached(scope=scope, trace_id=trace_id),
        )

    def _public_status_routes_uncached(
        self, *, scope: str = "live", trace_id: str | None = None
    ) -> Dict[str, Any]:
        """Rider-facing route status list.

        Returns one status record per active corridor with a plain-language
        severity tier, wording, and headline.  Suitable for status pages,
        digital signage, and third-party app integrations.
        """
        entities = self.transit_entities(scope=scope, trace_id=trace_id)
        regimes_payload = self.transit_regimes(scope=scope, trace_id=trace_id)
        incidents_payload = self.transit_incidents(scope=scope, trace_id=trace_id)

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

        # Include active + scheduled-later lines in the public status surface
        all_lines = (entities.get("active_lines") or []) + (
            entities.get("scheduled_later_lines") or []
        )
        # Deduplicate by entity_id (active_lines and lines may overlap)
        seen: set = set()
        unique_lines = []
        for line in all_lines:
            if not isinstance(line, dict):
                continue
            eid = str(line.get("entity_id") or "")
            if eid and eid not in seen:
                seen.add(eid)
                unique_lines.append(line)

        routes = [
            build_route_status(line, regime_by_entity, incidents_by_entity).to_json()
            for line in unique_lines
        ]
        # Sort: most severe first, then alphabetical by label
        routes.sort(
            key=lambda r: (
                -severity_rank(r.get("severity", "good")),
                str(r.get("label") or ""),
            )
        )

        return {
            "generated_at": isoformat_ms(),
            "scope": scope,
            "route_count": len(routes),
            "routes": routes,
        }

    def public_status_network(
        self, *, scope: str = "live", trace_id: str | None = None
    ) -> Dict[str, Any]:
        read_model = self._live_read_model(
            "status:network", scope=scope, trace_id=trace_id
        )
        if read_model:
            return read_model
        return self._cached_payload(
            ("public_status_network", scope, trace_id),
            lambda: self._public_status_network_uncached(scope=scope, trace_id=trace_id),
        )

    def _public_status_network_uncached(
        self, *, scope: str = "live", trace_id: str | None = None
    ) -> Dict[str, Any]:
        """Rider-facing network-level status summary.

        A single summary object for the whole network — suitable for a
        top-of-page banner or push notification about overall service quality.
        """
        health = self.transit_health(scope=scope, trace_id=trace_id)
        routes_payload = self.public_status_routes(scope=scope, trace_id=trace_id)

        route_severities = [
            str(r.get("severity") or "good") for r in routes_payload.get("routes") or []
        ]
        network_severity = classify_network_severity(route_severities)

        active_count = int(
            health.get("active_line_count") or health.get("line_count") or 0
        )
        incident_count = int(health.get("incident_count") or 0)
        critical_count = int(health.get("critical_incidents") or 0)

        disrupted_routes = [
            {
                "entity_id": r.get("entity_id"),
                "label": r.get("label"),
                "severity": r.get("severity"),
            }
            for r in (routes_payload.get("routes") or [])
            if r.get("severity") in ("delay", "disruption", "severe")
        ]

        return {
            "generated_at": isoformat_ms(),
            "scope": scope,
            "severity": network_severity,
            "severity_label": SEVERITY_LABELS.get(network_severity, network_severity),
            "severity_color": SEVERITY_COLOR.get(network_severity, "gray"),
            "active_route_count": active_count,
            "incident_count": incident_count,
            "critical_incident_count": critical_count,
            "disrupted_route_count": len(disrupted_routes),
            "disrupted_routes": disrupted_routes,
            "feed_status": health.get("feed_status"),
        }

    def public_status_feed_quality(
        self, *, scope: str = "live", trace_id: str | None = None
    ) -> Dict[str, Any]:
        return self._cached_payload(
            ("public_status_feed_quality", scope, trace_id),
            lambda: self._public_status_feed_quality_uncached(
                scope=scope, trace_id=trace_id
            ),
        )

    def _public_status_feed_quality_uncached(
        self, *, scope: str = "live", trace_id: str | None = None
    ) -> Dict[str, Any]:
        network = self.public_status_network(scope=scope, trace_id=trace_id)
        feed_status = dict(network.get("feed_status") or {})
        updated_at = feed_status.get("updated_at")
        age_seconds = _age_seconds(updated_at)
        active_route_count = int(network.get("active_route_count") or 0)
        vehicle_count = int(feed_status.get("vehicle_count") or 0)
        trip_update_count = int(feed_status.get("trip_update_count") or 0)
        alert_count = int(feed_status.get("alert_count") or 0)
        feed_status = {
            "feed_label": feed_status.get("feed_label"),
            "updated_at": updated_at,
            "agency_key": feed_status.get("agency_key"),
            "vehicle_count": vehicle_count,
            "trip_update_count": trip_update_count,
            "alert_count": alert_count,
            "collection_source": str(feed_status.get("collection_source") or ""),
            "status": str(feed_status.get("status") or "unknown"),
        }

        checks = [
            _feed_quality_check(
                "freshness",
                "Feed freshness",
                _freshness_status(age_seconds),
                _freshness_detail(age_seconds),
            ),
            _feed_quality_check(
                "route_coverage",
                "Route coverage",
                "good" if active_route_count > 0 else "disruption",
                f"{active_route_count} routes currently scoreable",
            ),
            _feed_quality_check(
                "vehicle_positions",
                "Vehicle positions",
                "good" if vehicle_count > 0 else "disruption",
                f"{vehicle_count} vehicles read from the latest sample",
            ),
            _feed_quality_check(
                "trip_updates",
                "Trip updates",
                "good" if trip_update_count > 0 else "advisory",
                f"{trip_update_count} trip updates read from the latest sample",
            ),
            _feed_quality_check(
                "alerts",
                "Service alerts",
                "good",
                f"{alert_count} service alerts read from the latest sample",
            ),
        ]
        check_statuses = {str(check.get("status") or "unknown") for check in checks}
        if "disruption" in check_statuses:
            status = "disruption"
        elif "advisory" in check_statuses:
            status = "advisory"
        elif "unknown" in check_statuses:
            status = "unknown"
        else:
            status = "good"
        return {
            "generated_at": isoformat_ms(),
            "scope": scope,
            "status": status,
            "status_label": SEVERITY_LABELS.get(status, status),
            "status_color": SEVERITY_COLOR.get(status, "gray"),
            "updated_at": updated_at,
            "age_seconds": age_seconds,
            "checks": checks,
            "feed_status": feed_status,
        }

    def public_status_triage(
        self,
        *,
        scope: str = "live",
        trace_id: str | None = None,
        limit: int = 12,
    ) -> Dict[str, Any]:
        return self._cached_payload(
            ("public_status_triage", scope, trace_id, int(limit)),
            lambda: self._public_status_triage_uncached(
                scope=scope, trace_id=trace_id, limit=limit
            ),
        )

    def _public_status_triage_uncached(
        self,
        *,
        scope: str = "live",
        trace_id: str | None = None,
        limit: int = 12,
    ) -> Dict[str, Any]:
        routes_payload = self.public_status_routes(scope=scope, trace_id=trace_id)
        candidates = [
            dict(route)
            for route in (routes_payload.get("routes") or [])
            if isinstance(route, dict)
            and str(route.get("severity") or "good") not in ("good", "unknown")
        ]
        candidates.sort(key=_triage_sort_key)
        rows = []
        for rank, route in enumerate(candidates[: max(1, min(int(limit), 50))], start=1):
            severity = str(route.get("severity") or "unknown")
            rows.append(
                {
                    "rank": rank,
                    "entity_id": route.get("entity_id"),
                    "route_id": route.get("route_id"),
                    "label": route.get("label"),
                    "severity": severity,
                    "severity_label": route.get("severity_label")
                    or SEVERITY_LABELS.get(severity, severity),
                    "headline": route.get("headline"),
                    "short_summary": route.get("short_summary"),
                    "hazard_score": route.get("hazard_score"),
                    "active_alert_count": int(route.get("active_alert_count") or 0),
                    "median_delay_seconds": route.get("median_delay_seconds"),
                    "updated_at_ms": route.get("timestamp_ms"),
                    "evidence": _triage_evidence(route),
                    "recommended_action": _public_recommended_action(severity),
                }
            )
        return {
            "generated_at": isoformat_ms(),
            "scope": scope,
            "triage_count": len(rows),
            "routes": rows,
        }

    def public_status_alerts(
        self, *, scope: str = "live", trace_id: str | None = None
    ) -> Dict[str, Any]:
        return self._cached_payload(
            ("public_status_alerts", scope, trace_id),
            lambda: self._public_status_alerts_uncached(scope=scope, trace_id=trace_id),
        )

    def _public_status_alerts_uncached(
        self, *, scope: str = "live", trace_id: str | None = None
    ) -> Dict[str, Any]:
        """Rider-facing active alerts list.

        Returns only the incidents that have crossed the incident threshold,
        formatted as plain-language advisory text.  No internal scoring
        vocabulary is exposed.
        """
        incidents_payload = self.transit_incidents(scope=scope, trace_id=trace_id)
        entities_payload = self.transit_entities(scope=scope, trace_id=trace_id)

        # Build label index from lines
        label_by_entity: Dict[str, str] = {}
        for line in (entities_payload.get("lines") or []) + (
            entities_payload.get("active_lines") or []
        ):
            if isinstance(line, dict) and line.get("entity_id"):
                label_by_entity[str(line["entity_id"])] = str(
                    line.get("label") or line.get("route_id") or line["entity_id"]
                )

        alerts = []
        for incident in incidents_payload.get("incidents") or []:
            if not isinstance(incident, dict):
                continue
            eid = str(incident.get("entity_id") or "")
            route_label = label_by_entity.get(eid) or str(incident.get("label") or eid)
            severity = incident.get("severity") or "minor"
            # Map internal severity to public tier
            public_severity = {
                "critical": "disruption",
                "major": "disruption",
                "minor": "delay",
                "info": "advisory",
            }.get(str(severity).lower(), "advisory")

            alerts.append(
                {
                    "alert_id": incident.get("incident_id"),
                    "entity_id": eid,
                    "route_label": route_label,
                    "severity": public_severity,
                    "severity_label": SEVERITY_LABELS.get(
                        public_severity, public_severity
                    ),
                    "severity_color": SEVERITY_COLOR.get(public_severity, "gray"),
                    "headline": str(incident.get("summary") or "Service alert"),
                    "recommended_action": str(incident.get("recommended_action") or ""),
                    "timestamp_ms": incident.get("timestamp_ms"),
                }
            )

        # Sort: most severe first, most recent first within tier
        def _sort_key(a: Dict[str, Any]):
            sev = str(a.get("severity") or "advisory")
            ranks = {"disruption": 3, "delay": 2, "advisory": 1, "good": 0}
            ts = int(a.get("timestamp_ms") or 0)
            return (-ranks.get(sev, 0), -ts)

        alerts.sort(key=_sort_key)

        return {
            "generated_at": isoformat_ms(),
            "scope": scope,
            "alert_count": len(alerts),
            "alerts": alerts,
        }

    def public_status_map(
        self, *, scope: str = "live", trace_id: str | None = None
    ) -> Dict[str, Any]:
        """Public live map payload — vehicle positions + corridor geometry.

        Returns the same GeoJSON-compatible shape as the protected /api/transit/map
        endpoint, but scoped to live only and with public-facing property labels.
        """
        return self._cached_payload(
            ("public_status_map", scope, trace_id),
            lambda: self._public_status_map_uncached(scope=scope, trace_id=trace_id),
        )

    def _public_status_map_uncached(
        self, *, scope: str = "live", trace_id: str | None = None
    ) -> Dict[str, Any]:
        """Public live map endpoint — vehicle positions + corridor geometry."""
        # Reuse the existing map builder but strip internal tokens if present
        map_payload = self._transit_map_uncached(scope=scope, trace_id=trace_id)
        return map_payload

    def public_status_scorecard(
        self,
        *,
        scope: str = "live",
        trace_id: str | None = None,
        limit: int = 720,
    ) -> Dict[str, Any]:
        return self._cached_payload(
            ("public_status_scorecard", scope, trace_id, int(limit)),
            lambda: self._public_status_scorecard_uncached(
                scope=scope, trace_id=trace_id, limit=limit
            ),
        )

    def _public_status_scorecard_uncached(
        self,
        *,
        scope: str = "live",
        trace_id: str | None = None,
        limit: int = 720,
    ) -> Dict[str, Any]:
        """Public reliability scorecard.

        Suitable for agency websites, weekly service reports, and rider apps
        that want historical reliability data without internal scoring detail.
        """
        scorecard = self.transit_scorecard(scope=scope, trace_id=trace_id, limit=limit)

        # Strip internal regime/action vocabulary, keep only public-ready fields
        public_corridors = []
        for corridor in scorecard.get("corridors") or []:
            if not isinstance(corridor, dict):
                continue
            public_corridors.append(
                {
                    "entity_id": corridor.get("entity_id"),
                    "label": corridor.get("label"),
                    "route_id": corridor.get("route_id"),
                    "on_time_pct": corridor.get("on_time_pct"),
                    "avg_delay_seconds": corridor.get("avg_delay_seconds"),
                    "incident_count": corridor.get("incident_count"),
                    "snapshot_count": corridor.get("snapshot_count"),
                    "healthy_pct": corridor.get("healthy_pct"),
                    "unstable_pct": corridor.get("unstable_pct"),
                }
            )

        net = scorecard.get("network") or {}
        return {
            "generated_at": isoformat_ms(),
            "scope": scope,
            "window_snapshots": scorecard.get("window_snapshots"),
            "corridor_count": scorecard.get("corridor_count"),
            "total_incidents": scorecard.get("total_incidents"),
            "network": {
                "on_time_pct": net.get("on_time_pct"),
                "healthy_pct": net.get("healthy_pct"),
                "unstable_pct": net.get("unstable_pct"),
                "avg_delay_seconds": net.get("avg_delay_seconds"),
                "unstable_corridor_count": net.get("unstable_corridor_count"),
            },
            "corridors": public_corridors,
        }

    def transit_map(
        self, *, scope: str = "all", trace_id: str | None = None
    ) -> Dict[str, Any]:
        return self._transit_map_uncached(scope=scope, trace_id=trace_id)

    def _transit_map_uncached(
        self, *, scope: str = "all", trace_id: str | None = None
    ) -> Dict[str, Any]:
        """Return a GeoJSON-compatible map payload for the dashboard map view.

        Combines:
        - vehicle positions (lat/lon) tagged with corridor regime and hazard
        - corridor regime summary keyed by entity_id
        - active incidents keyed by entity_id
        """
        entities = self.transit_entities(scope=scope, trace_id=trace_id)
        regimes_payload = self.transit_regimes(scope=scope, trace_id=trace_id)
        incidents_payload = self.transit_incidents(scope=scope, trace_id=trace_id)

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
            direction_id = (
                obs.get("direction_id")
                if isinstance(obs, dict)
                else v.get("direction_id")
            )
            corridor_entity_id = str(v.get("corridor_entity_id") or "").strip()
            fallback_corridor_keys = [
                corridor_entity_id,
                (
                    f"route:{route_id}:{int(direction_id)}"
                    if route_id and direction_id not in (None, "")
                    else ""
                ),
                f"route:{route_id}:all" if route_id else "",
            ]
            regime_rec = {}
            vehicle_regime = v.get("regime")
            if isinstance(vehicle_regime, dict):
                regime_rec = vehicle_regime
            if not regime_rec:
                regime_rec = next(
                    (
                        regime_by_entity.get(key) or {}
                        for key in fallback_corridor_keys
                        if key
                    ),
                    {},
                )
            vehicle_features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "entity_id": v.get("entity_id"),
                        "vehicle_id": vehicle_id,
                        "route_id": route_id,
                        "direction_id": direction_id,
                        "corridor_entity_id": corridor_entity_id or None,
                        "corridor_id": v.get("corridor_id"),
                        "route_label": v.get("route_label"),
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
                        "hazard_score": regime_rec.get("hazard_score")
                        or regime_rec.get("hazard"),
                        "regime": regime_rec.get("regime"),
                        "label": v.get("label") or v.get("route_label") or route_id,
                        "timestamp_ms": obs.get("timestamp_ms")
                        if isinstance(obs, dict)
                        else None,
                    },
                }
            )

        # Build corridor features and summaries
        corridor_map_features = []
        corridor_features = []
        for line in _unique_corridor_rows(entities):
            if not isinstance(line, dict):
                continue
            eid = str(line.get("entity_id") or "")
            regime_rec = regime_by_entity.get(eid) or {}
            active_incidents = incidents_by_entity.get(eid) or []
            corridor_summary = {
                "entity_id": eid,
                "route_id": line.get("route_id"),
                "direction_id": line.get("direction_id"),
                "label": line.get("label") or line.get("route_id"),
                "regime": regime_rec.get("regime") or line.get("regime"),
                "hazard_score": regime_rec.get("hazard_score")
                or line.get("hazard")
                or line.get("avg_hazard"),
                "active_vehicles": line.get("active_vehicle_count")
                or line.get("vehicle_count"),
                "incident_count": len(active_incidents),
                "top_action": regime_rec.get("action") or line.get("top_action"),
                "timestamp_ms": regime_rec.get("timestamp_ms")
                or line.get("timestamp_ms"),
                "activity_status": line.get("activity_status"),
                "corridor_id": line.get("corridor_id"),
                "route_mode": line.get("route_mode"),
            }
            corridor_features.append(corridor_summary)
            geometry = line.get("geometry")
            if _is_supported_corridor_geometry(geometry):
                corridor_map_features.append(
                    {
                        "type": "Feature",
                        "geometry": geometry,
                        "properties": {
                            **corridor_summary,
                            "_color": _regime_color(
                                corridor_summary.get("regime"),
                                corridor_summary.get("hazard_score"),
                            ),
                        },
                    }
                )

        return {
            "type": "FeatureCollection",
            "scope": scope,
            "trace_id": trace_id,
            "timestamp": isoformat_ms(),
            "vehicle_features": vehicle_features,
            "corridor_features": corridor_map_features,
            "corridor_summaries": corridor_features,
            "vehicle_count": len(vehicle_features),
            "corridor_count": len(corridor_features),
        }


def _unique_corridor_rows(entities: Dict[str, Any]) -> list[Dict[str, Any]]:
    rows = []
    seen: set[str] = set()
    for key in ["active_lines", "scheduled_later_lines", "inactive_lines", "lines"]:
        for line in entities.get(key) or []:
            if not isinstance(line, dict):
                continue
            entity_id = str(line.get("entity_id") or "").strip()
            if not entity_id or entity_id in seen:
                continue
            seen.add(entity_id)
            rows.append(line)
    return rows


def _dashboard_entities_payload(entities: Dict[str, Any]) -> Dict[str, Any]:
    """Return the fields the browser console needs without map geometry bulk."""
    return {
        "generated_at": entities.get("generated_at"),
        "agency_key": entities.get("agency_key"),
        "lines": [
            _dashboard_corridor_payload(row)
            for row in (entities.get("lines") or [])
            if isinstance(row, dict)
        ],
        "scheduled_later_lines": [
            _dashboard_corridor_payload(row)
            for row in (entities.get("scheduled_later_lines") or [])
            if isinstance(row, dict)
        ],
        "inactive_lines": [
            _dashboard_corridor_payload(row)
            for row in (entities.get("inactive_lines") or [])
            if isinstance(row, dict)
        ],
        "vehicles": [
            _dashboard_vehicle_payload(row)
            for row in (entities.get("vehicles") or [])
            if isinstance(row, dict)
        ],
    }


def _dashboard_corridor_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    keys = (
        "entity_id",
        "agency_key",
        "corridor_id",
        "route_id",
        "direction_id",
        "label",
        "vehicle_count",
        "median_delay_seconds",
        "scheduled_headway_seconds",
        "compressed_headway_share",
        "avg_delay_seconds",
        "top_action",
        "top_action_label",
        "avg_hazard",
        "active_alert_count",
        "current_regime",
        "current_regime_label",
        "priority_score",
        "priority_label",
        "activity_status",
        "activity_status_label",
        "activity_reason",
        "activity_reason_label",
        "route_mode",
        "source",
        "collection_source",
        "trace_id",
        "timestamp_ms",
    )
    return {key: row.get(key) for key in keys if key in row}


def _dashboard_vehicle_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    keys = (
        "entity_id",
        "label",
        "vehicle_id",
        "corridor_entity_id",
        "agency_key",
        "corridor_id",
        "route_id",
        "route_label",
        "trip_id",
        "direction_id",
        "stop_id",
        "status",
        "delay_seconds",
        "occupancy_status",
        "source",
        "collection_source",
    )
    payload = {key: row.get(key) for key in keys if key in row}
    regime = row.get("regime")
    if isinstance(regime, dict):
        payload["regime"] = {
            key: regime.get(key)
            for key in (
                "entity_id",
                "label",
                "route_id",
                "regime",
                "regime_label",
                "hazard",
                "action",
                "action_label",
                "confidence",
                "priority_score",
                "priority_label",
                "timestamp_ms",
            )
            if key in regime
        }
    return payload


def _is_supported_corridor_geometry(geometry: Any) -> bool:
    if not isinstance(geometry, dict):
        return False
    if str(geometry.get("type") or "") != "LineString":
        return False
    coordinates = geometry.get("coordinates")
    return isinstance(coordinates, list) and len(coordinates) >= 2


def _regime_color(regime: Any, hazard_score: Any) -> str:
    regime_name = str(regime or "").strip().lower()
    if regime_name == "healthy":
        return "#22c55e"
    if regime_name == "bunching_onset":
        return "#f59e0b"
    if regime_name in {"headway_collapse", "service_degraded"}:
        return "#ef4444"
    if regime_name == "terminal_congestion":
        return "#f97316"
    if regime_name == "stop_dwell_instability":
        return "#a855f7"
    if regime_name == "corridor_unstable":
        return "#ec4899"
    if regime_name == "feed_incoherent":
        return "#6b7280"
    try:
        hazard = float(hazard_score or 0.0)
    except (TypeError, ValueError):
        hazard = 0.0
    if hazard >= 0.75:
        return "#ef4444"
    if hazard >= 0.45:
        return "#f59e0b"
    return "#3b82f6"


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _age_seconds(timestamp: Any) -> int | None:
    if not timestamp:
        return None
    try:
        value = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - value).total_seconds()))


def _freshness_status(age_seconds: int | None) -> str:
    if age_seconds is None:
        return "unknown"
    if age_seconds <= 120:
        return "good"
    if age_seconds <= 300:
        return "advisory"
    return "disruption"


def _freshness_detail(age_seconds: int | None) -> str:
    if age_seconds is None:
        return "No feed timestamp available"
    if age_seconds < 60:
        return f"Updated {age_seconds}s ago"
    return f"Updated {round(age_seconds / 60)}m ago"


def _feed_quality_check(
    check_id: str, label: str, status: str, detail: str
) -> Dict[str, Any]:
    return {
        "check_id": check_id,
        "label": label,
        "status": status,
        "status_label": SEVERITY_LABELS.get(status, status),
        "detail": detail,
    }


def _triage_sort_key(route: Dict[str, Any]) -> tuple:
    severity = str(route.get("severity") or "good")
    hazard = _number_or_zero(route.get("hazard_score"))
    alert_count = int(route.get("active_alert_count") or 0)
    delay = abs(_number_or_zero(route.get("median_delay_seconds")))
    timestamp = int(route.get("timestamp_ms") or 0)
    label = str(route.get("label") or "")
    return (-severity_rank(severity), -alert_count, -hazard, -delay, -timestamp, label)


def _triage_evidence(route: Dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    alert_count = int(route.get("active_alert_count") or 0)
    if alert_count:
        suffix = "" if alert_count == 1 else "s"
        evidence.append(f"{alert_count} active alert{suffix}")
    delay = route.get("median_delay_seconds")
    if isinstance(delay, (int, float)) and abs(delay) >= 60:
        evidence.append(f"median delay {round(delay / 60)}m")
    hazard = route.get("hazard_score")
    if isinstance(hazard, (int, float)) and hazard >= 0.2:
        evidence.append(f"risk score {hazard:.2f}")
    advisories = [str(item) for item in (route.get("advisories") or []) if item]
    if advisories:
        evidence.append(advisories[0])
    if not evidence:
        evidence.append(str(route.get("headline") or "Route elevated by live status"))
    return evidence[:4]


def _public_recommended_action(severity: str) -> str:
    if severity in ("severe", "disruption"):
        return "Escalate for operations review and align rider messaging."
    if severity == "delay":
        return "Monitor service and publish delay guidance if it persists."
    if severity == "advisory":
        return "Watch for corroborating changes in vehicles, trips, and alerts."
    return "No public action needed."


def _number_or_zero(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


class TransitAPIHandler(BaseHTTPRequestHandler):
    server_version = "TransitSentinel/1.0"

    @property
    def svc(self) -> TransitAPIService:  # type: ignore[override]
        return getattr(self.server, "transit_service")

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        etag = f'"{hashlib.sha256(body).hexdigest()}"'
        conditional_get = (
            status == 200
            and self.command == "GET"
            and str(os.getenv("TRANSIT_API_ETAG_ENABLED", "1")).strip().lower()
            not in {"0", "false", "no", "off"}
        )
        if conditional_get and self._etag_matches(etag):
            self.send_response(304)
            self._send_cors_headers()
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if conditional_get:
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_cors_headers(self) -> None:
        self.send_header(
            "Access-Control-Allow-Origin",
            os.getenv("TRANSIT_API_ALLOW_ORIGIN", "*"),
        )
        self.send_header(
            "Access-Control-Allow-Headers",
            os.getenv(
                "TRANSIT_API_ALLOW_HEADERS",
                "Authorization, Content-Type, If-None-Match, Cache-Control",
            ),
        )
        self.send_header(
            "Access-Control-Allow-Methods",
            os.getenv("TRANSIT_API_ALLOW_METHODS", "GET, POST, OPTIONS"),
        )
        self.send_header(
            "Access-Control-Expose-Headers",
            os.getenv("TRANSIT_API_EXPOSE_HEADERS", "ETag"),
        )

    def _etag_matches(self, etag: str) -> bool:
        raw = self.headers.get("If-None-Match") or self.headers.get("if-none-match")
        if not raw:
            return False
        return "*" in {part.strip() for part in raw.split(",")} or etag in {
            part.strip() for part in raw.split(",")
        }

    def do_OPTIONS(self) -> None:  # pragma: no cover - browser interoperability
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def _get_bearer_token(self) -> str | None:
        """Extract the raw Authorization header from the request."""
        return self.headers.get("Authorization") or self.headers.get("authorization")

    def _require_role(self, required_role: str) -> tuple[bool, str | None, str | None]:
        """Check auth and send a 401 response if not authorised.

        Returns (authorised, token, role).  Caller must check the bool before
        continuing; if False, the 401 has already been sent.
        """
        auth_header = self._get_bearer_token()
        ok, token, role = check_auth(auth_header, required_role=required_role)
        if not ok:
            self._send_json(
                {"error": "unauthorized", "required_role": required_role}, status=401
            )
        return ok, token, role

    def do_GET(self) -> None:  # pragma: no cover - exercised via integration tests
        # Check request size limit (1MB max for query string and headers)
        # Note: For GET requests, body size is typically 0, but we still check headers
        content_length = int(self.headers.get("Content-Length") or 0)
        if content_length > 1024 * 1024:  # 1MB
            self._send_json({"error": "request_too_large"}, status=413)
            return

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query or "")
        scope = (params.get("scope") or ["all"])[0]
        trace_id = (params.get("trace_id") or [""])[0] or None

        # Health and public status endpoints are always open (no auth required)
        if parsed.path in {"/health", "/api/health", "/api/status"}:
            self._send_json(self.svc.service_health())
            return

        # Public service-status API — rider-facing, no auth required
        if parsed.path.startswith("/api/status/"):
            status_scope = scope if scope not in ("", "all") else "live"
            if parsed.path == "/api/status/routes":
                self._send_json(
                    self.svc.public_status_routes(scope=status_scope, trace_id=trace_id)
                )
                return
            if parsed.path == "/api/status/network":
                self._send_json(
                    self.svc.public_status_network(
                        scope=status_scope, trace_id=trace_id
                    )
                )
                return
            if parsed.path == "/api/status/feed-quality":
                self._send_json(
                    self.svc.public_status_feed_quality(
                        scope=status_scope, trace_id=trace_id
                    )
                )
                return
            if parsed.path == "/api/status/triage":
                limit_raw = (params.get("limit") or ["12"])[0]
                try:
                    triage_limit = int(limit_raw)
                except ValueError:
                    triage_limit = 12
                self._send_json(
                    self.svc.public_status_triage(
                        scope=status_scope,
                        trace_id=trace_id,
                        limit=max(1, min(triage_limit, 50)),
                    )
                )
                return
            if parsed.path == "/api/status/alerts":
                self._send_json(
                    self.svc.public_status_alerts(scope=status_scope, trace_id=trace_id)
                )
                return
            if parsed.path == "/api/status/map":
                self._send_json(
                    self.svc.public_status_map(scope=status_scope, trace_id=trace_id)
                )
                return
            if parsed.path == "/api/status/scorecard":
                limit_raw = (params.get("limit") or ["720"])[0]
                try:
                    sc_limit = int(limit_raw)
                except ValueError:
                    sc_limit = 720
                self._send_json(
                    self.svc.public_status_scorecard(
                        scope=status_scope,
                        trace_id=trace_id,
                        limit=max(1, min(sc_limit, 2000)),
                    )
                )
                return
            self._send_json({"error": "not_found"}, status=404)
            return

        # Ops endpoints require at least viewer role
        ok, _token, _role = self._require_role(ROLE_VIEWER)
        if not ok:
            return

        if parsed.path == "/api/transit/dashboard":
            self._send_json(self.svc.transit_dashboard(scope=scope, trace_id=trace_id))
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
        if parsed.path == "/api/transit/audit":
            # Audit trail requires admin role
            ok_admin, _t, _r = self._require_role(ROLE_ADMIN)
            if not ok_admin:
                return
            limit_raw = (params.get("limit") or ["100"])[0]
            try:
                a_limit = int(limit_raw)
            except ValueError:
                a_limit = 100
            self._send_json(self.svc.audit_trail(limit=max(1, min(a_limit, 500))))
            return

        self._send_json({"error": "not_found"}, status=404)

    def do_POST(self) -> None:  # pragma: no cover - exercised via integration tests
        # Check request size limit (1MB max)
        content_length = int(self.headers.get("Content-Length") or 0)
        if content_length > 1024 * 1024:  # 1MB
            self._send_json({"error": "request_too_large"}, status=413)
            return

        parsed = urlparse(self.path)

        # Incident acknowledgement — requires operator role
        if parsed.path == "/api/transit/incidents/ack":
            ok, token, role = self._require_role(ROLE_OPERATOR)
            if not ok:
                return
            content_length = int(self.headers.get("Content-Length") or 0)
            body_raw = self.rfile.read(content_length) if content_length else b"{}"
            try:
                body = json.loads(body_raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send_json({"error": "invalid_json"}, status=400)
                return
            incident_id = str(body.get("incident_id") or "").strip()
            if not incident_id:
                self._send_json({"error": "missing_incident_id"}, status=400)
                return
            note = str(body.get("note") or "")
            result = self.svc.acknowledge_incident(
                incident_id,
                note=note,
                acknowledged_by=str(token or ""),
            )
            self._send_json(result)
            return

        if parsed.path == "/api/transit/incidents/feedback":
            ok, token, _role = self._require_role(ROLE_OPERATOR)
            if not ok:
                return
            body_raw = self.rfile.read(content_length) if content_length else b"{}"
            try:
                body = json.loads(body_raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send_json({"error": "invalid_json"}, status=400)
                return
            incident_id = str(body.get("incident_id") or "").strip()
            disposition = str(body.get("disposition") or "").strip().lower()
            result = self.svc.record_incident_feedback(
                incident_id,
                disposition=disposition,
                cause=str(body.get("cause") or ""),
                action_taken=str(body.get("action_taken") or ""),
                note=str(body.get("note") or ""),
                recorded_by=str(token or ""),
            )
            self._send_json(result, status=400 if result.get("error") else 200)
            return

        # Default: 404 for any other POST
        self._send_json({"error": "not_found"}, status=404)

    def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover
        logger.info("%s - %s", self.address_string(), format % args)


def start_transit_http_server(
    service: TransitAPIService, host: str = "0.0.0.0", port: int = 8000
) -> HTTPServer:
    class _Server(ThreadingHTTPServer):  # pragma: no cover
        daemon_threads = True
        allow_reuse_address = True
        request_queue_size = _int_env("TRANSIT_API_REQUEST_QUEUE_SIZE", 32)

        def __init__(self, address):
            super().__init__(address, TransitAPIHandler)
            self.transit_service = service
            self.request_gate = threading.BoundedSemaphore(
                max(1, _int_env("TRANSIT_API_MAX_CONCURRENT_REQUESTS", 8))
            )

        def process_request(self, request: Any, client_address: Any) -> None:
            if not self.request_gate.acquire(blocking=False):
                try:
                    body = b'{"error":"server_busy"}'
                    request.sendall(
                        b"HTTP/1.1 503 Service Unavailable\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Connection: close\r\n"
                        + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
                        + body
                    )
                except OSError:
                    pass
                request.close()
                return
            try:
                super().process_request(request, client_address)
            except Exception:
                self.request_gate.release()
                raise

        def process_request_thread(self, request: Any, client_address: Any) -> None:
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.request_gate.release()

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
