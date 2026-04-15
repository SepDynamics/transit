"""Transit-facing summaries, regimes, and incidents for the scaffold API."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from scripts.shared.runtime import clamp, isoformat_ms
from scripts.transit.agencies import (
    default_transit_agency_key,
    get_transit_agency_adapter,
)
from scripts.transit.case_packs import load_event_overlays, summarize_matching_overlays
from scripts.transit.feeds import (
    load_gtfs_catalog,
    load_gtfs_realtime_resource,
    merge_realtime_bundles,
)
from scripts.transit.transit_types import (
    GTFSStaticCatalog,
    TransitAlertObservation,
    TransitCorridorSnapshot,
    TransitFeedStatus,
    TransitIncidentRecord,
    TransitRegimeRecord,
    TransitRealtimeBundle,
    TransitStopTimeUpdate,
    TransitTripUpdateObservation,
    TransitVehicleSnapshot,
    TransitVehicleObservation,
)


TRANSIT_ACTION_PRIORITY = {
    "dispatch_relief": 5,
    "short_turn": 4,
    "inspect_terminal": 3,
    "hold": 2,
    "warn_riders": 1,
    "mark_feed_degraded": 1,
    "monitor": 0,
}

TRANSIT_ACTION_LABELS = {
    "dispatch_relief": "Dispatch relief",
    "short_turn": "Short turn",
    "inspect_terminal": "Inspect terminal",
    "hold": "Hold to rebalance",
    "warn_riders": "Publish rider advisory",
    "mark_feed_degraded": "Mark telemetry degraded",
    "monitor": "Monitor",
}

TRANSIT_REGIME_LABELS = {
    "healthy": "Stable service",
    "recovering": "Recovering service",
    "data_sparse": "Limited telemetry",
    "bunching_onset": "Early bunching",
    "corridor_unstable": "Service irregularity",
    "headway_collapse": "Severe bunching / service gap",
    "service_degraded": "Confirmed disruption",
    "terminal_congestion": "Terminal congestion",
    "stop_dwell_instability": "Extended dwell",
    "terminal_blocked": "Terminal blocked",
    "feed_incoherent": "Telemetry degraded",
}

TRANSIT_ACTIVITY_STATUS_LABELS = {
    "active_now": "Active now",
    "scheduled_later": "Scheduled later",
    "inactive": "Inactive",
}

TRANSIT_ACTIVITY_REASON_LABELS = {
    "live_telemetry": "Live telemetry present",
    "scheduled_no_telemetry": "Scheduled with no live telemetry",
    "service_starts_later": "Service starts later today",
    "returns_next_service_day": "Returns next service day",
    "inactive": "Outside the service window",
}

SERVICE_IMPACT_ALERT_EFFECTS = {
    "DETOUR",
    "STOP_MOVED",
    "REDUCED_SERVICE",
    "NO_SERVICE",
    "MODIFIED_SERVICE",
}
HIGH_IMPACT_ALERT_EFFECTS = {
    "DETOUR",
    "REDUCED_SERVICE",
    "NO_SERVICE",
    "MODIFIED_SERVICE",
}
SERVICE_ALERT_KEYWORDS = (
    "delay",
    "behind schedule",
    "longer travel times",
    "shuttle",
    "replace service",
    "replace train service",
    "replace line service",
    "detour",
    "skipping",
    "skip",
    "no service",
    "terminate",
    "terminating",
    "suspended",
    "suspension",
    "stop moved",
)
FACILITY_ALERT_KEYWORDS = (
    "elevator",
    "escalator",
    "pedal & park",
    "parking",
    "parking lot",
    "parking garage",
    "garage",
    "stairwell",
    "lobby",
    "pedestrian bridge",
    "bike rack",
    "bicycle rack",
    "entrance",
    "accessible parking",
    "platform access",
)


def _humanize_transit_token(value: str | None, *, fallback: str = "Unknown") -> str:
    token = str(value or "").strip()
    if not token:
        return fallback
    return token.replace("_", " ")


def transit_action_label(action: str | None) -> str:
    token = str(action or "").strip()
    return TRANSIT_ACTION_LABELS.get(token, _humanize_transit_token(token))


def transit_regime_label(regime: str | None) -> str:
    token = str(regime or "").strip()
    return TRANSIT_REGIME_LABELS.get(token, _humanize_transit_token(token))


def transit_activity_status_label(status: str | None) -> str:
    token = str(status or "").strip()
    return TRANSIT_ACTIVITY_STATUS_LABELS.get(token, _humanize_transit_token(token))


def transit_activity_reason_label(reason: str | None) -> str:
    token = str(reason or "").strip()
    return TRANSIT_ACTIVITY_REASON_LABELS.get(token, _humanize_transit_token(token))


def _operational_priority_score(
    *,
    regime: str,
    action: str,
    hazard: float,
    confidence: float,
    metrics: Dict[str, Any],
) -> int:
    action_base = {
        "dispatch_relief": 60,
        "short_turn": 56,
        "inspect_terminal": 50,
        "hold": 44,
        "warn_riders": 34,
        "mark_feed_degraded": 28,
        "monitor": 16 if regime != "healthy" else 0,
    }.get(action, 10)
    regime_bonus = {
        "headway_collapse": 24,
        "terminal_congestion": 20,
        "stop_dwell_instability": 16,
        "service_degraded": 14,
        "corridor_unstable": 12,
        "bunching_onset": 10,
        "feed_incoherent": 12,
        "healthy": 0,
    }.get(regime, 6 if regime else 0)
    alert_bonus = min(
        18,
        (int(metrics.get("high_impact_alert_count") or 0) * 8)
        + (int(metrics.get("active_alert_count") or 0) * 4),
    )
    delay_bonus = min(
        12,
        int(max(0.0, float(metrics.get("median_delay_seconds") or 0.0)) / 90.0),
    )
    headway_bonus = 0
    compressed_headway_share = float(metrics.get("compressed_headway_share") or 0.0)
    if compressed_headway_share >= 0.75:
        headway_bonus = 6
    elif compressed_headway_share >= 0.5:
        headway_bonus = 3
    telemetry_bonus = (
        4
        if regime == "feed_incoherent" and bool(metrics.get("scheduled_service_active"))
        else 0
    )
    raw = (
        action_base
        + regime_bonus
        + alert_bonus
        + delay_bonus
        + headway_bonus
        + telemetry_bonus
        + int(round(clamp(hazard) * 18))
    )
    confidence_factor = 0.7 + (clamp(confidence) * 0.3)
    return max(0, min(100, int(round(raw * confidence_factor))))


def _priority_label(priority_score: int) -> str:
    if priority_score >= 85:
        return "Immediate"
    if priority_score >= 65:
        return "High"
    if priority_score >= 45:
        return "Watch"
    return "Monitor"


@dataclass
class TransitRuntimeConfig:
    system_name: str
    agency_key: str = default_transit_agency_key()
    static_feed: Optional[str] = None
    vehicle_positions_feed: Optional[str] = None
    trip_updates_feed: Optional[str] = None
    alerts_feed: Optional[str] = None
    event_overlays_feed: Optional[str] = None
    stale_after_seconds: int = 90
    feed_timezone: str = "UTC"


class TransitSnapshotService:
    """Build transit-native dashboard payloads from GTFS and GTFS-RT inputs."""

    def __init__(self, config: Optional[TransitRuntimeConfig] = None) -> None:
        if config is None:
            adapter = get_transit_agency_adapter(
                os.getenv("TRANSIT_AGENCY", default_transit_agency_key())
            )
            system_name = os.getenv("TRANSIT_SYSTEM_NAME", adapter.system_name)
            default_feed_paths = adapter.default_feed_paths()
            config = TransitRuntimeConfig(
                system_name=system_name,
                agency_key=os.getenv("TRANSIT_AGENCY", adapter.key),
                static_feed=_default_current_feed(
                    "TRANSIT_GTFS_STATIC_PATH", Path(default_feed_paths["static_gtfs"])
                ),
                vehicle_positions_feed=_default_current_feed(
                    "TRANSIT_GTFS_RT_VEHICLE_POSITIONS_PATH",
                    Path(default_feed_paths["vehicle_positions"]),
                ),
                trip_updates_feed=_default_current_feed(
                    "TRANSIT_GTFS_RT_TRIP_UPDATES_PATH",
                    Path(default_feed_paths["trip_updates"]),
                ),
                alerts_feed=_default_current_feed(
                    "TRANSIT_GTFS_RT_ALERTS_PATH", Path(default_feed_paths["alerts"])
                ),
                event_overlays_feed=os.getenv("TRANSIT_EVENT_OVERLAYS_PATH") or None,
                stale_after_seconds=max(
                    30, int(os.getenv("TRANSIT_FEED_STALE_AFTER_SECONDS", "90"))
                ),
                feed_timezone=os.getenv(
                    "TRANSIT_FEED_TIMEZONE",
                    adapter.timezone_name or _default_feed_timezone(system_name),
                ),
            )
        self.cfg = config
        self._catalog_cache: Dict[str, Any] = {}
        self._snapshot_cache: Dict[str, Any] = {}
        self._snapshot_cache_ttl = _float_env("TRANSIT_SNAPSHOT_CACHE_TTL_SECONDS", 2.0)

    def service_health(self) -> Dict[str, Any]:
        snapshot = self._build_snapshot()
        return {
            "service": "Transit Sentinel API",
            "timestamp": isoformat_ms(),
            "system_name": self.cfg.system_name,
            "status": "ok" if not snapshot["errors"] else "degraded",
            "feed_status": snapshot["feed_status"],
            "errors": snapshot["errors"],
        }

    def snapshot(
        self,
        *,
        scope: str = "all",
        trace_id: str | None = None,
        now_ms: int | None = None,
    ) -> Dict[str, Any]:
        return self._build_snapshot(scope=scope, trace_id=trace_id, now_ms=now_ms)

    def health(
        self,
        *,
        scope: str = "all",
        trace_id: str | None = None,
        now_ms: int | None = None,
    ) -> Dict[str, Any]:
        snapshot = self._build_snapshot(scope=scope, trace_id=trace_id, now_ms=now_ms)
        return snapshot["health"]

    def entities(
        self,
        *,
        scope: str = "all",
        trace_id: str | None = None,
        now_ms: int | None = None,
    ) -> Dict[str, Any]:
        snapshot = self._build_snapshot(scope=scope, trace_id=trace_id, now_ms=now_ms)
        return snapshot["entities"]

    def regimes(
        self,
        *,
        scope: str = "all",
        trace_id: str | None = None,
        now_ms: int | None = None,
    ) -> Dict[str, Any]:
        snapshot = self._build_snapshot(scope=scope, trace_id=trace_id, now_ms=now_ms)
        return snapshot["regimes"]

    def incidents(
        self,
        *,
        scope: str = "all",
        trace_id: str | None = None,
        now_ms: int | None = None,
    ) -> Dict[str, Any]:
        snapshot = self._build_snapshot(scope=scope, trace_id=trace_id, now_ms=now_ms)
        return snapshot["incidents"]

    def history(
        self,
        *,
        entity_id: str,
        scope: str = "all",
        trace_id: str | None = None,
        limit: int = 72,
        now_ms: int | None = None,
    ) -> Dict[str, Any]:
        snapshot = self._build_snapshot(scope=scope, trace_id=trace_id, now_ms=now_ms)
        vehicles = snapshot["entities"]["vehicles"]
        vehicle = next((row for row in vehicles if row["entity_id"] == entity_id), None)
        corridor_entity_id = (
            str(vehicle.get("corridor_entity_id") or "") if vehicle else ""
        )
        regime = next(
            (
                row
                for row in snapshot["regimes"]["regimes"]
                if str(row.get("entity_id") or "") == corridor_entity_id
            ),
            None,
        )
        observations = (
            [vehicle["observation"]] if vehicle and vehicle.get("observation") else []
        )
        regimes = [regime] if regime else []
        return {
            "generated_at": isoformat_ms(),
            "scope": scope,
            "trace_id": trace_id,
            "entity": vehicle or {"entity_id": entity_id},
            "observations": observations[-limit:],
            "regimes": regimes[-limit:],
        }

    def sources(self) -> Dict[str, Any]:
        configured = {
            "static_gtfs": bool(self.cfg.static_feed),
            "vehicle_positions": bool(self.cfg.vehicle_positions_feed),
            "trip_updates": bool(self.cfg.trip_updates_feed),
            "alerts": bool(self.cfg.alerts_feed),
            "event_overlays": bool(self.cfg.event_overlays_feed),
        }
        return {
            "generated_at": isoformat_ms(),
            "scopes": [
                {"id": "all", "label": "All feeds"},
                {"id": "live", "label": "Live feed"},
            ],
            "available": {"live": any(configured.values()), "replay": False},
            "configured_feeds": configured,
            "agency_key": self.cfg.agency_key,
            "traces": [],
            "trace_ids": [],
        }

    def _build_snapshot(
        self,
        *,
        scope: str = "all",
        trace_id: str | None = None,
        now_ms: int | None = None,
    ) -> Dict[str, Any]:
        del scope, trace_id
        source_stamp = self._source_stamp()
        built_at = float(self._snapshot_cache.get("built_at") or 0.0)
        if (
            self._snapshot_cache_ttl > 0
            and
            now_ms is None
            and self._snapshot_cache.get("stamp") == source_stamp
            and (time.monotonic() - built_at) <= self._snapshot_cache_ttl
        ):
            return dict(self._snapshot_cache["payload"])
        errors: List[str] = []
        catalog = GTFSStaticCatalog(feed_label="transit-feed")
        bundles: List[TransitRealtimeBundle] = []

        if self.cfg.static_feed:
            try:
                catalog = self._load_catalog(self.cfg.static_feed)
            except Exception as exc:  # pragma: no cover - exercised via API runtime
                errors.append(f"static_gtfs: {exc}")
        if self.cfg.vehicle_positions_feed:
            try:
                bundles.append(
                    load_gtfs_realtime_resource(
                        self.cfg.vehicle_positions_feed,
                        feed_label="vehicle_positions",
                        collection_source="gtfs_rt_vehicle_positions",
                        payload_type="vehicle_positions",
                    )
                )
            except Exception as exc:  # pragma: no cover - exercised via API runtime
                errors.append(f"vehicle_positions: {exc}")
        if self.cfg.trip_updates_feed:
            try:
                bundles.append(
                    load_gtfs_realtime_resource(
                        self.cfg.trip_updates_feed,
                        feed_label="trip_updates",
                        collection_source="gtfs_rt_trip_updates",
                        payload_type="trip_updates",
                    )
                )
            except Exception as exc:  # pragma: no cover - exercised via API runtime
                errors.append(f"trip_updates: {exc}")
        if self.cfg.alerts_feed:
            try:
                bundles.append(
                    load_gtfs_realtime_resource(
                        self.cfg.alerts_feed,
                        feed_label="alerts",
                        collection_source="gtfs_rt_alerts",
                        payload_type="alerts",
                    )
                )
            except Exception as exc:  # pragma: no cover - exercised via API runtime
                errors.append(f"alerts: {exc}")

        bundle = (
            merge_realtime_bundles(self.cfg.system_name, *bundles)
            if bundles
            else TransitRealtimeBundle(
                feed_label=self.cfg.system_name,
                feed_timestamp_ms=None,
                collection_source="gtfs_rt",
            )
        )
        snapshot_now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        event_overlays = load_event_overlays(self.cfg.event_overlays_feed)
        augmented_trip_updates = _enrich_trip_updates(
            catalog,
            bundle.trip_updates,
            timezone_name=self.cfg.feed_timezone,
            now_ms=snapshot_now_ms,
        )
        augmented_vehicles = _attach_trip_update_delays(
            catalog, bundle.vehicles, augmented_trip_updates
        )
        filtered_trip_updates = _filter_trip_updates(
            catalog,
            augmented_trip_updates,
            augmented_vehicles,
            now_ms=snapshot_now_ms,
            timezone_name=self.cfg.feed_timezone,
        )
        deduped_trip_updates = _dedupe_trip_updates(
            catalog,
            filtered_trip_updates,
            augmented_vehicles,
            now_ms=snapshot_now_ms,
            timezone_name=self.cfg.feed_timezone,
        )
        route_rows, route_regimes, route_incidents = _score_routes(
            catalog=catalog,
            vehicles=augmented_vehicles,
            trip_updates=deduped_trip_updates,
            alerts=bundle.alerts,
            agency_key=self.cfg.agency_key,
            event_overlays=event_overlays,
            now_ms=snapshot_now_ms,
            stale_after_seconds=self.cfg.stale_after_seconds,
            feed_timezone=self.cfg.feed_timezone,
        )
        active_lines, scheduled_later_lines, inactive_lines = _partition_route_rows(
            route_rows
        )
        vehicles = _build_vehicle_rows(
            catalog,
            augmented_vehicles,
            route_regimes,
            agency_key=self.cfg.agency_key,
            event_overlays=event_overlays,
        )
        recurring = _build_recurring_signatures(route_regimes)
        feed_status = _build_feed_status(bundle, errors, agency_key=self.cfg.agency_key)
        health = _build_health_payload(
            system_name=self.cfg.system_name,
            route_rows=route_rows,
            active_route_rows=active_lines,
            scheduled_later_route_rows=scheduled_later_lines,
            inactive_route_rows=inactive_lines,
            vehicle_rows=vehicles,
            route_regimes=route_regimes,
            incidents=route_incidents,
            feed_status=feed_status,
        )
        payload = {
            "errors": errors,
            "feed_status": feed_status,
            "health": health,
            "entities": {
                "generated_at": isoformat_ms(),
                "agency_key": self.cfg.agency_key,
                "lines": active_lines,
                "active_lines": active_lines,
                "scheduled_later_lines": scheduled_later_lines,
                "inactive_lines": inactive_lines,
                "vehicles": vehicles,
                "event_overlays": event_overlays,
            },
            "regimes": {
                "generated_at": isoformat_ms(),
                "regimes": [row.to_json() for row in route_regimes],
                "recurring_regimes": recurring,
            },
            "incidents": {
                "generated_at": isoformat_ms(),
                "incidents": [row.to_json() for row in route_incidents],
            },
        }
        if now_ms is None:
            self._snapshot_cache = {
                "stamp": source_stamp,
                "built_at": time.monotonic(),
                "payload": payload,
            }
        return payload

    def _load_catalog(self, source: str) -> GTFSStaticCatalog:
        mtime_ns = _local_mtime_ns(source)
        load_options = _gtfs_load_options()
        cache_key = f"{source}:{mtime_ns}:{json.dumps(load_options, sort_keys=True)}"
        if self._catalog_cache.get("key") == cache_key:
            return self._catalog_cache["catalog"]
        catalog = load_gtfs_catalog(
            source,
            feed_label=Path(source).stem or "transit-feed",
            **load_options,
        )
        self._catalog_cache = {"key": cache_key, "catalog": catalog}
        return catalog

    def _source_stamp(self) -> Tuple[Any, ...]:
        return tuple(
            (source, _local_mtime_ns(source))
            for source in [
                self.cfg.static_feed,
                self.cfg.vehicle_positions_feed,
                self.cfg.trip_updates_feed,
                self.cfg.alerts_feed,
                self.cfg.event_overlays_feed,
            ]
            if source
        )


def _attach_trip_update_delays(
    catalog: GTFSStaticCatalog,
    vehicles: Sequence[TransitVehicleObservation],
    trip_updates: Sequence[TransitTripUpdateObservation],
) -> List[TransitVehicleObservation]:
    by_trip = {row.trip_id: row for row in trip_updates if row.trip_id}
    by_vehicle = {row.vehicle_id: row for row in trip_updates if row.vehicle_id}
    merged: List[TransitVehicleObservation] = []
    for vehicle in vehicles:
        if vehicle.delay_seconds is not None:
            merged.append(vehicle)
            continue
        match = None
        if vehicle.trip_id and vehicle.trip_id in by_trip:
            match = by_trip[vehicle.trip_id]
        elif vehicle.vehicle_id in by_vehicle:
            match = by_vehicle[vehicle.vehicle_id]
        payload = vehicle.to_json()
        if not payload.get("route_id"):
            payload["route_id"] = catalog.trip_route_id(vehicle.trip_id)
        if payload.get("direction_id") is None:
            payload["direction_id"] = catalog.trip_direction_id(vehicle.trip_id)
        if match and match.delay_seconds is not None:
            payload["delay_seconds"] = match.delay_seconds
        merged.append(TransitVehicleObservation(**payload))
    return merged


def _enrich_trip_updates(
    catalog: GTFSStaticCatalog,
    trip_updates: Sequence[TransitTripUpdateObservation],
    timezone_name: str,
    now_ms: int,
) -> List[TransitTripUpdateObservation]:
    enriched: List[TransitTripUpdateObservation] = []
    for update in trip_updates:
        payload = update.to_json()
        if not payload.get("route_id"):
            payload["route_id"] = catalog.trip_route_id(update.trip_id)
        if payload.get("direction_id") is None:
            payload["direction_id"] = catalog.trip_direction_id(update.trip_id)
        route_mode = catalog.route_mode(payload.get("route_id"))
        lookback_ms, lookahead_ms = _trip_update_relevance_window_ms(route_mode)
        derived_delay_candidates: List[Tuple[int, int]] = []
        fallback_delays: List[int] = []
        stop_time_updates: List[TransitStopTimeUpdate] = []
        for stop_update in update.stop_time_updates:
            stop_payload = {
                "stop_id": stop_update.stop_id,
                "stop_sequence": stop_update.stop_sequence,
                "arrival_time_unix": stop_update.arrival_time_unix,
                "departure_time_unix": stop_update.departure_time_unix,
                "arrival_delay_seconds": stop_update.arrival_delay_seconds,
                "departure_delay_seconds": stop_update.departure_delay_seconds,
                "schedule_relationship": stop_update.schedule_relationship,
            }
            if (
                stop_payload["arrival_delay_seconds"] is None
                and stop_payload["arrival_time_unix"] is not None
            ):
                stop_payload["arrival_delay_seconds"] = _derive_delay_from_schedule(
                    catalog,
                    trip_id=update.trip_id,
                    service_date=update.service_date,
                    timezone_name=timezone_name,
                    stop_sequence=stop_update.stop_sequence,
                    stop_id=stop_update.stop_id,
                    scheduled_event="arrival",
                    realtime_unix=stop_update.arrival_time_unix,
                )
            if (
                stop_payload["departure_delay_seconds"] is None
                and stop_payload["departure_time_unix"] is not None
            ):
                stop_payload["departure_delay_seconds"] = _derive_delay_from_schedule(
                    catalog,
                    trip_id=update.trip_id,
                    service_date=update.service_date,
                    timezone_name=timezone_name,
                    stop_sequence=stop_update.stop_sequence,
                    stop_id=stop_update.stop_id,
                    scheduled_event="departure",
                    realtime_unix=stop_update.departure_time_unix,
                )
            event_ms = _stop_update_event_time_ms(
                catalog,
                update,
                stop_payload,
                timezone_name=timezone_name,
            )
            for candidate in [
                stop_payload["arrival_delay_seconds"],
                stop_payload["departure_delay_seconds"],
            ]:
                if candidate is None:
                    continue
                fallback_delays.append(int(candidate))
                if event_ms is not None and (now_ms - lookback_ms) <= event_ms <= (
                    now_ms + lookahead_ms
                ):
                    derived_delay_candidates.append(
                        (abs(event_ms - now_ms), int(candidate))
                    )
            stop_time_updates.append(TransitStopTimeUpdate(**stop_payload))
        payload["stop_time_updates"] = stop_time_updates
        if payload.get("delay_seconds") is None:
            if derived_delay_candidates:
                derived_delay_candidates.sort(key=lambda row: (row[0], -abs(row[1])))
                payload["delay_seconds"] = derived_delay_candidates[0][1]
            elif fallback_delays:
                payload["delay_seconds"] = max(
                    fallback_delays, key=lambda value: abs(value)
                )
        enriched.append(TransitTripUpdateObservation(**payload))
    return enriched


def _filter_trip_updates(
    catalog: GTFSStaticCatalog,
    trip_updates: Sequence[TransitTripUpdateObservation],
    vehicles: Sequence[TransitVehicleObservation],
    *,
    now_ms: int,
    timezone_name: str,
) -> List[TransitTripUpdateObservation]:
    live_trip_ids = {row.trip_id for row in vehicles if row.trip_id}
    live_vehicle_ids = {row.vehicle_id for row in vehicles if row.vehicle_id}
    filtered: List[TransitTripUpdateObservation] = []
    for update in trip_updates:
        if _trip_update_is_relevant(
            catalog,
            update,
            live_trip_ids=live_trip_ids,
            live_vehicle_ids=live_vehicle_ids,
            now_ms=now_ms,
            timezone_name=timezone_name,
        ):
            filtered.append(update)
    return filtered


def _dedupe_trip_updates(
    catalog: GTFSStaticCatalog,
    trip_updates: Sequence[TransitTripUpdateObservation],
    vehicles: Sequence[TransitVehicleObservation],
    *,
    now_ms: int,
    timezone_name: str,
) -> List[TransitTripUpdateObservation]:
    live_trip_ids = {row.trip_id for row in vehicles if row.trip_id}
    live_vehicle_ids = {row.vehicle_id for row in vehicles if row.vehicle_id}
    grouped: Dict[Tuple[str, str], List[TransitTripUpdateObservation]] = {}
    passthrough: List[TransitTripUpdateObservation] = []
    for update in trip_updates:
        chain_key = _trip_chain_key(catalog, update)
        if chain_key is None:
            passthrough.append(update)
            continue
        grouped.setdefault(chain_key, []).append(update)

    deduped: List[TransitTripUpdateObservation] = list(passthrough)
    for updates in grouped.values():
        if len(updates) == 1:
            deduped.extend(updates)
            continue
        updates.sort(
            key=lambda row: _trip_update_chain_rank(
                catalog,
                row,
                live_trip_ids=live_trip_ids,
                live_vehicle_ids=live_vehicle_ids,
                now_ms=now_ms,
                timezone_name=timezone_name,
            ),
            reverse=True,
        )
        deduped.append(updates[0])
    return deduped


def _trip_update_is_relevant(
    catalog: GTFSStaticCatalog,
    update: TransitTripUpdateObservation,
    *,
    live_trip_ids: set[str],
    live_vehicle_ids: set[str],
    now_ms: int,
    timezone_name: str,
) -> bool:
    if update.trip_id and update.trip_id in live_trip_ids:
        return True
    if update.vehicle_id and update.vehicle_id in live_vehicle_ids:
        return True
    route_id = update.route_id or catalog.trip_route_id(update.trip_id)
    route_mode = catalog.route_mode(route_id)
    lookback_ms, lookahead_ms = _trip_update_relevance_window_ms(route_mode)
    schedule_window = catalog.trip_schedule_window_epoch_seconds(
        update.trip_id,
        service_date=update.service_date,
        timezone_name=timezone_name,
    )
    if schedule_window:
        schedule_start_ms = schedule_window[0] * 1000
        schedule_end_ms = schedule_window[1] * 1000
        if schedule_end_ms >= (now_ms - lookback_ms) and schedule_start_ms <= (
            now_ms + lookahead_ms
        ):
            return True
        return False
    realtime_window = _trip_update_realtime_window_ms(update)
    if realtime_window:
        return realtime_window[1] >= (now_ms - lookback_ms) and realtime_window[0] <= (
            now_ms + lookahead_ms
        )
    return bool(update.delay_seconds is not None)


def _trip_update_relevance_window_ms(route_mode: str) -> Tuple[int, int]:
    if route_mode in {"bus", "light_rail", "subway"}:
        return 45 * 60 * 1000, 90 * 60 * 1000
    if route_mode in {"commuter_rail", "ferry"}:
        return 90 * 60 * 1000, 180 * 60 * 1000
    return 60 * 60 * 1000, 120 * 60 * 1000


def _trip_chain_key(
    catalog: GTFSStaticCatalog,
    update: TransitTripUpdateObservation,
) -> Optional[Tuple[str, str]]:
    if update.vehicle_id:
        return ("vehicle", update.vehicle_id)
    trip = catalog.trips.get(update.trip_id)
    if trip and trip.block_id:
        return ("block", trip.block_id)
    return None


def _trip_update_chain_rank(
    catalog: GTFSStaticCatalog,
    update: TransitTripUpdateObservation,
    *,
    live_trip_ids: set[str],
    live_vehicle_ids: set[str],
    now_ms: int,
    timezone_name: str,
) -> Tuple[int, int, int, int, int]:
    is_live_trip = int(bool(update.trip_id and update.trip_id in live_trip_ids))
    is_live_vehicle = int(
        bool(update.vehicle_id and update.vehicle_id in live_vehicle_ids)
    )
    event_distance = _trip_update_distance_from_now_ms(
        catalog,
        update,
        now_ms=now_ms,
        timezone_name=timezone_name,
    )
    stop_update_count = len(update.stop_time_updates)
    delay_signal = abs(int(update.delay_seconds or 0))
    return (
        is_live_trip,
        is_live_vehicle,
        -event_distance,
        stop_update_count,
        delay_signal,
    )


def _trip_update_realtime_window_ms(
    update: TransitTripUpdateObservation,
) -> Optional[Tuple[int, int]]:
    timestamps_ms: List[int] = []
    for stop_update in update.stop_time_updates:
        for candidate in [
            stop_update.arrival_time_unix,
            stop_update.departure_time_unix,
        ]:
            if candidate is not None:
                timestamps_ms.append(int(candidate) * 1000)
    if not timestamps_ms:
        return None
    return min(timestamps_ms), max(timestamps_ms)


def _trip_update_distance_from_now_ms(
    catalog: GTFSStaticCatalog,
    update: TransitTripUpdateObservation,
    *,
    now_ms: int,
    timezone_name: str,
) -> int:
    realtime_window = _trip_update_realtime_window_ms(update)
    if realtime_window:
        midpoint_ms = int((realtime_window[0] + realtime_window[1]) / 2)
        return abs(midpoint_ms - now_ms)
    schedule_window = catalog.trip_schedule_window_epoch_seconds(
        update.trip_id,
        service_date=update.service_date,
        timezone_name=timezone_name,
    )
    if schedule_window:
        midpoint_ms = int(
            ((schedule_window[0] * 1000) + (schedule_window[1] * 1000)) / 2
        )
        return abs(midpoint_ms - now_ms)
    return 24 * 60 * 60 * 1000


def _stop_update_event_time_ms(
    catalog: GTFSStaticCatalog,
    update: TransitTripUpdateObservation,
    stop_payload: Dict[str, Any],
    *,
    timezone_name: str,
) -> Optional[int]:
    timestamps_ms: List[int] = []
    for candidate in [
        stop_payload.get("arrival_time_unix"),
        stop_payload.get("departure_time_unix"),
    ]:
        if candidate is not None:
            timestamps_ms.append(int(candidate) * 1000)
    if timestamps_ms:
        return min(timestamps_ms)
    for event in ["arrival", "departure"]:
        scheduled_unix = catalog.scheduled_epoch_seconds(
            update.trip_id,
            service_date=update.service_date,
            timezone_name=timezone_name,
            stop_sequence=stop_payload.get("stop_sequence"),
            stop_id=stop_payload.get("stop_id"),
            event=event,
        )
        if scheduled_unix is not None:
            return int(scheduled_unix) * 1000
    return None


def _score_routes(
    *,
    catalog: GTFSStaticCatalog,
    vehicles: Sequence[TransitVehicleObservation],
    trip_updates: Sequence[TransitTripUpdateObservation],
    alerts: Sequence[TransitAlertObservation],
    agency_key: str,
    event_overlays: Sequence[Dict[str, Any]],
    now_ms: int,
    stale_after_seconds: int,
    feed_timezone: str,
) -> Tuple[
    List[Dict[str, Any]], List[TransitRegimeRecord], List[TransitIncidentRecord]
]:
    route_groups: Dict[Tuple[str, Optional[int]], Dict[str, Any]] = {}
    for vehicle in vehicles:
        route_id = vehicle.route_id or "unassigned"
        key = (route_id, vehicle.direction_id)
        group = route_groups.setdefault(
            key,
            {
                "route_id": route_id,
                "direction_id": vehicle.direction_id,
                "vehicles": [],
                "trip_updates": [],
                "alerts": [],
            },
        )
        group["vehicles"].append(vehicle)
    for update in trip_updates:
        route_id = update.route_id or "unassigned"
        key = (route_id, update.direction_id)
        group = route_groups.setdefault(
            key,
            {
                "route_id": route_id,
                "direction_id": update.direction_id,
                "vehicles": [],
                "trip_updates": [],
                "alerts": [],
            },
        )
        group["trip_updates"].append(update)
    for alert in alerts:
        if alert.route_ids:
            for route_id in alert.route_ids:
                matching_keys = [
                    group_key for group_key in route_groups if group_key[0] == route_id
                ]
                if not matching_keys:
                    matching_keys = [(route_id, None)]
                for key in matching_keys:
                    group = route_groups.setdefault(
                        key,
                        {
                            "route_id": route_id,
                            "direction_id": key[1],
                            "vehicles": [],
                            "trip_updates": [],
                            "alerts": [],
                        },
                    )
                    group["alerts"].append(alert)
        else:
            key = ("network", None)
            group = route_groups.setdefault(
                key,
                {
                    "route_id": "network",
                    "direction_id": None,
                    "vehicles": [],
                    "trip_updates": [],
                    "alerts": [],
                },
            )
            group["alerts"].append(alert)

    route_rows: List[Dict[str, Any]] = []
    route_regimes: List[TransitRegimeRecord] = []
    incidents: List[TransitIncidentRecord] = []
    for key, group in route_groups.items():
        route_id = str(group["route_id"])
        direction_id = group["direction_id"]
        corridor_id = _corridor_id(agency_key, route_id, direction_id)
        corridor_entity_id = _corridor_entity_id(route_id, direction_id)
        route_vehicles: List[TransitVehicleObservation] = list(group["vehicles"])
        route_updates: List[TransitTripUpdateObservation] = list(group["trip_updates"])
        route_alerts: List[TransitAlertObservation] = list(group["alerts"])
        route_label = _corridor_label(
            catalog, route_id, direction_id, route_vehicles, route_updates
        )
        metrics = _compute_route_metrics(
            catalog=catalog,
            agency_key=agency_key,
            route_id=route_id,
            corridor_id=corridor_id,
            direction_id=direction_id,
            vehicles=route_vehicles,
            trip_updates=route_updates,
            alerts=route_alerts,
            now_ms=now_ms,
            stale_after_seconds=stale_after_seconds,
            feed_timezone=feed_timezone,
        )
        activity_status, activity_reason = _route_activity_status(
            catalog,
            route_id=route_id,
            direction_id=direction_id,
            metrics=metrics,
            now_ms=now_ms,
            timezone_name=feed_timezone,
        )
        regime, reasons = _classify_route(metrics)
        action = _recommended_action(regime, metrics)
        hazard_components = _hazard_components(metrics)
        hazard = _hazard_score(hazard_components)
        if regime == "feed_incoherent":
            hazard = max(hazard, 0.78)
        elif metrics["low_observation"]:
            hazard *= 0.72
        confidence, provenance = _build_provenance(metrics, hazard_components)
        priority_score = _operational_priority_score(
            regime=regime,
            action=action,
            hazard=hazard,
            confidence=confidence,
            metrics=metrics,
        )
        priority_label = _priority_label(priority_score)
        matched_event_overlays = summarize_matching_overlays(
            list(event_overlays),
            route_id=route_id,
            corridor_id=corridor_id,
            agency_key=agency_key,
        )
        signature_basis = {
            "route_id": route_id,
            "direction_id": direction_id,
            "regime": regime,
            "action": action,
            "reasons": reasons[:4],
        }
        signature = hashlib.sha1(
            json.dumps(signature_basis, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        record = TransitRegimeRecord(
            timestamp_ms=int(metrics["latest_timestamp_ms"] or now_ms),
            entity_id=corridor_entity_id,
            entity_type="corridor",
            label=route_label,
            agency_key=agency_key,
            corridor_id=corridor_id,
            route_id=route_id,
            regime=regime,
            hazard=round(hazard, 4),
            action=action,
            scoring_backend="heuristic_v1",
            confidence=round(confidence, 4),
            signature=signature,
            reasons=reasons,
            provenance=provenance,
            metrics=metrics,
            collection_source="gtfs_rt",
            event_overlays=matched_event_overlays,
        )
        route_regimes.append(record)
        route_rows.append(
            TransitCorridorSnapshot(
                timestamp_ms=record.timestamp_ms,
                entity_id=record.entity_id,
                agency_key=agency_key,
                corridor_id=corridor_id,
                route_id=route_id,
                direction_id=direction_id,
                label=route_label,
                route_mode=str(metrics["route_mode"]),
                vehicle_count=int(metrics["vehicle_count"]),
                median_delay_seconds=int(metrics["median_delay_seconds"]),
                scheduled_headway_seconds=(
                    int(metrics["scheduled_headway_seconds"])
                    if metrics["scheduled_headway_seconds"] not in (None, "")
                    else None
                ),
                compressed_headway_share=float(metrics["compressed_headway_share"]),
                avg_delay_seconds=float(metrics["avg_delay_seconds"]),
                top_action=action,
                avg_hazard=record.hazard,
                active_alert_count=int(metrics["active_alert_count"]),
                activity_status=activity_status,
                activity_reason=activity_reason,
                source=record.source,
                collection_source=record.collection_source,
                trace_id=record.trace_id,
                geometry=catalog.route_geometry(route_id, direction_id),
                current_regime=regime,
                current_regime_label=transit_regime_label(regime),
                top_action_label=transit_action_label(action),
                priority_score=priority_score,
                priority_label=priority_label,
                activity_status_label=transit_activity_status_label(activity_status),
                activity_reason_label=transit_activity_reason_label(activity_reason),
                event_overlays=matched_event_overlays,
            ).to_json()
        )
        record.priority_score = priority_score
        record.priority_label = priority_label
        record.regime_label = transit_regime_label(regime)
        record.action_label = transit_action_label(action)
        if regime != "healthy" and (
            action != "monitor" or (record.hazard >= 0.62 and record.confidence >= 0.55)
        ):
            incidents.append(
                TransitIncidentRecord(
                    incident_id=f"{record.entity_id}:{action}:{regime}",
                    timestamp_ms=record.timestamp_ms,
                    entity_id=record.entity_id,
                    entity_type=record.entity_type,
                    label=record.label,
                    agency_key=agency_key,
                    corridor_id=corridor_id,
                    route_id=route_id,
                    severity=_severity_for_action(action, record.hazard),
                    action=action,
                    regime=regime,
                    hazard=record.hazard,
                    confidence=record.confidence,
                    summary=_incident_summary(record, metrics),
                    recommended_action=_recommended_text(action),
                    reasons=reasons,
                    provenance=record.provenance,
                    priority_score=priority_score,
                    priority_label=priority_label,
                    regime_label=record.regime_label,
                    action_label=record.action_label,
                    event_overlays=matched_event_overlays,
                )
            )

    route_rows.sort(
        key=lambda item: (
            -int(item.get("priority_score") or 0),
            -float(item.get("avg_hazard") or 0.0),
            -int(item.get("active_alert_count") or 0),
            item["label"],
        )
    )
    route_regimes.sort(
        key=lambda item: (-int(item.priority_score or 0), -item.hazard, item.label)
    )
    incidents.sort(
        key=lambda item: (
            -int(item.priority_score or 0),
            -TRANSIT_ACTION_PRIORITY.get(item.action, 0),
            -item.hazard,
            item.label,
        )
    )
    return route_rows, route_regimes, incidents


def _partition_route_rows(
    route_rows: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    active_lines = [
        row for row in route_rows if row.get("activity_status") == "active_now"
    ]
    scheduled_later_lines = [
        row for row in route_rows if row.get("activity_status") == "scheduled_later"
    ]
    inactive_lines = [
        row for row in route_rows if row.get("activity_status") == "inactive"
    ]
    return active_lines, scheduled_later_lines, inactive_lines


def _compute_route_metrics(
    *,
    catalog: GTFSStaticCatalog,
    agency_key: str,
    route_id: str,
    corridor_id: str,
    direction_id: Optional[int],
    vehicles: Sequence[TransitVehicleObservation],
    trip_updates: Sequence[TransitTripUpdateObservation],
    alerts: Sequence[TransitAlertObservation],
    now_ms: int,
    stale_after_seconds: int,
    feed_timezone: str,
) -> Dict[str, Any]:
    delays = [
        int(vehicle.delay_seconds or 0)
        for vehicle in vehicles
        if vehicle.delay_seconds is not None
    ]
    if not delays:
        delays = [
            int(update.delay_seconds or 0)
            for update in trip_updates
            if update.delay_seconds is not None
        ]
    avg_delay = round(sum(delays) / len(delays), 2) if delays else 0.0
    median_delay = _quantile(delays, 0.5)
    p90_delay = _quantile(delays, 0.9)
    delay_spread = (
        (max(delays) - min(delays))
        if len(delays) >= 2
        else (max(delays) if delays else 0)
    )
    route_vehicles = sorted(
        vehicles,
        key=lambda row: (
            row.current_stop_sequence
            if row.current_stop_sequence is not None
            else math.inf,
            row.timestamp_ms,
            row.vehicle_id,
        ),
    )
    convoy_pairs = 0
    for left, right in zip(route_vehicles, route_vehicles[1:]):
        if left.current_stop_sequence is None or right.current_stop_sequence is None:
            continue
        if abs(right.current_stop_sequence - left.current_stop_sequence) <= 1:
            convoy_pairs += 1
    compressed_headway_share = (
        convoy_pairs / max(1, len(route_vehicles) - 1)
        if len(route_vehicles) > 1
        else 0.0
    )
    terminal_backlog = sum(
        1
        for vehicle in route_vehicles
        if (
            (
                vehicle.current_stop_sequence is not None
                and vehicle.current_stop_sequence <= 2
            )
            or str(vehicle.current_status or "").upper() == "STOPPED_AT"
        )
        and int(vehicle.delay_seconds or 0) >= 180
    )
    dwell_overruns = 0
    dwell_total = 0
    for update in trip_updates:
        for stop_update in update.stop_time_updates:
            arrival_delay = stop_update.arrival_delay_seconds
            departure_delay = stop_update.departure_delay_seconds
            if arrival_delay is None or departure_delay is None:
                continue
            dwell_total += 1
            if (departure_delay - arrival_delay) >= 90:
                dwell_overruns += 1
    dwell_share = dwell_overruns / dwell_total if dwell_total else 0.0
    scheduled_headway = catalog.scheduled_headway_seconds(route_id, direction_id)
    latest_timestamp_ms = max(
        [row.timestamp_ms for row in route_vehicles]
        + [row.timestamp_ms for row in trip_updates]
        + [row.timestamp_ms for row in alerts]
        + [0]
    )
    feed_age_seconds = (
        max(0.0, (now_ms - latest_timestamp_ms) / 1000.0)
        if latest_timestamp_ms
        else float(stale_after_seconds)
    )
    position_coverage = sum(
        1
        for row in route_vehicles
        if row.latitude is not None and row.longitude is not None
    ) / max(1, len(route_vehicles))
    trip_update_coverage = (
        min(1.0, len(trip_updates) / max(1, len(route_vehicles)))
        if route_vehicles
        else 0.0
    )
    alert_summary = _summarize_route_alerts(alerts)
    active_alert_count = alert_summary["service_alert_count"]
    route_mode = catalog.route_mode(route_id)
    route_type = catalog.route_type(route_id)
    scheduled_service_active = catalog.route_is_scheduled_active(
        route_id,
        direction_id=direction_id,
        timestamp_ms=int(latest_timestamp_ms or now_ms),
        timezone_name=feed_timezone,
    )
    low_observation = _is_low_observation(
        route_mode=route_mode,
        vehicle_count=len(route_vehicles),
        trip_update_count=len(trip_updates),
        active_alert_count=active_alert_count,
    )
    return {
        "agency_key": agency_key,
        "corridor_id": corridor_id,
        "direction_id": direction_id,
        "route_type": route_type,
        "route_mode": route_mode,
        "total_alert_count": alert_summary["total_alert_count"],
        "vehicle_count": len(route_vehicles),
        "trip_update_count": len(trip_updates),
        "active_alert_count": active_alert_count,
        "facility_alert_count": alert_summary["facility_alert_count"],
        "high_impact_alert_count": alert_summary["high_impact_alert_count"],
        "avg_delay_seconds": avg_delay,
        "median_delay_seconds": median_delay,
        "p90_delay_seconds": p90_delay,
        "delay_spread_seconds": delay_spread,
        "scheduled_headway_seconds": scheduled_headway,
        "compressed_headway_share": round(compressed_headway_share, 4),
        "terminal_backlog_count": terminal_backlog,
        "dwell_overrun_share": round(dwell_share, 4),
        "position_coverage": round(position_coverage, 4),
        "trip_update_coverage": round(trip_update_coverage, 4),
        "feed_age_seconds": round(feed_age_seconds, 2),
        "latest_timestamp_ms": latest_timestamp_ms or None,
        "stale_after_seconds": stale_after_seconds,
        "scheduled_service_active": scheduled_service_active,
        "low_observation": low_observation,
        "alert_only": bool(
            active_alert_count and not route_vehicles and not trip_updates
        ),
        "trip_updates_only": bool(trip_updates and not route_vehicles),
        "observations_present": bool(route_vehicles or trip_updates),
    }


def _classify_route(metrics: Dict[str, Any]) -> Tuple[str, List[str]]:
    reasons: List[str] = []
    service_delay_threshold = _service_delay_threshold(metrics)
    if not metrics["observations_present"] and not metrics["scheduled_service_active"]:
        return "healthy", ["healthy", "route_inactive"]
    if (
        not metrics["observations_present"]
        and metrics["facility_alert_count"] > 0
        and metrics["active_alert_count"] == 0
    ):
        return "healthy", ["healthy", "facility_advisory_only", "no_service_telemetry"]
    if (
        metrics["feed_age_seconds"] >= metrics["stale_after_seconds"]
        and not metrics["observations_present"]
    ):
        reasons.extend(["feed_incoherent", "feed_stale_or_sparse"])
        return "feed_incoherent", reasons
    if metrics["alert_only"]:
        if not metrics["scheduled_service_active"]:
            return "healthy", ["healthy", "route_inactive_alert"]
        if metrics["active_alert_count"] >= 2:
            reasons.extend(
                ["service_degraded", "service_alert_active", "stacked_alerts"]
            )
            return "service_degraded", reasons
        return "healthy", ["healthy", "alert_only"]
    if (
        metrics["trip_updates_only"]
        and metrics["feed_age_seconds"] < metrics["stale_after_seconds"]
    ):
        if _has_strong_trip_update_delay_signal(metrics):
            reasons.extend(
                ["service_degraded", "trip_updates_only", "persistent_delay"]
            )
            return "service_degraded", reasons
        return "healthy", ["healthy", "trip_updates_only"]
    if (
        metrics["feed_age_seconds"] >= metrics["stale_after_seconds"]
        and metrics["trip_update_count"] == 0
        and metrics["scheduled_service_active"]
    ):
        reasons.extend(["feed_incoherent", "vehicle_feed_stale"])
        return "feed_incoherent", reasons
    if (
        metrics["terminal_backlog_count"] >= 3
        and metrics["median_delay_seconds"] >= 180
    ):
        reasons.extend(["terminal_congestion", "terminal_backlog", "late_departures"])
        return "terminal_congestion", reasons
    if (
        metrics["dwell_overrun_share"] >= 0.35
        and metrics["median_delay_seconds"] >= 120
    ):
        reasons.extend(["stop_dwell_instability", "dwell_overrun"])
        return "stop_dwell_instability", reasons
    if (
        _supports_headway_regimes(metrics)
        and metrics["compressed_headway_share"] >= 0.75
        and metrics["p90_delay_seconds"] >= 420
    ):
        reasons.extend(["headway_collapse", "convoy_compression", "late_tail"])
        return "headway_collapse", reasons
    if (
        _supports_headway_regimes(metrics)
        and metrics["compressed_headway_share"] >= 0.5
        and metrics["median_delay_seconds"] >= 120
    ):
        reasons.extend(["bunching_onset", "compressed_headways"])
        return "bunching_onset", reasons
    if (
        metrics["vehicle_count"] >= 3
        and metrics["delay_spread_seconds"] >= 420
        and metrics["median_delay_seconds"] >= 120
    ) or (
        metrics["vehicle_count"] >= 4
        and metrics["compressed_headway_share"] >= 0.45
        and metrics["p90_delay_seconds"] >= 300
    ):
        reasons.extend(["corridor_unstable", "delay_whiplash"])
        return "corridor_unstable", reasons
    if _should_suppress_sparse_delay(metrics):
        return "healthy", ["healthy", "sparse_delay_signal"]
    if (
        (metrics["high_impact_alert_count"] >= 1 and _has_alert_corroboration(metrics))
        or (metrics["active_alert_count"] >= 2 and _has_alert_corroboration(metrics))
        or (
            metrics["active_alert_count"] > 0
            and metrics["median_delay_seconds"]
            >= max(180, int(service_delay_threshold * 0.75))
            and _has_alert_corroboration(metrics)
        )
        or (
            metrics["median_delay_seconds"] >= service_delay_threshold
            and _has_delay_corroboration(metrics)
        )
    ):
        reasons.extend(["service_degraded"])
        if metrics["active_alert_count"] > 0:
            reasons.append("service_alert_active")
        if metrics["high_impact_alert_count"] > 0:
            reasons.append("high_impact_alert")
        if metrics["median_delay_seconds"] >= service_delay_threshold:
            reasons.append("persistent_delay")
        return "service_degraded", reasons
    return "healthy", ["healthy"]


def _supports_headway_regimes(metrics: Dict[str, Any]) -> bool:
    return str(metrics.get("route_mode") or "") in {"bus", "light_rail", "subway"}


def _service_delay_threshold(metrics: Dict[str, Any]) -> int:
    route_mode = str(metrics.get("route_mode") or "other")
    scheduled_headway = float(metrics.get("scheduled_headway_seconds") or 0.0)
    if route_mode in {"light_rail", "subway"}:
        return int(
            max(
                300.0,
                min(600.0, scheduled_headway * 0.5 if scheduled_headway else 300.0),
            )
        )
    if route_mode == "bus":
        return int(
            max(
                420.0,
                min(900.0, scheduled_headway * 0.75 if scheduled_headway else 420.0),
            )
        )
    if route_mode in {"commuter_rail", "ferry"}:
        return int(
            max(
                900.0,
                min(1800.0, scheduled_headway * 0.5 if scheduled_headway else 900.0),
            )
        )
    return int(
        max(420.0, min(900.0, scheduled_headway * 0.6 if scheduled_headway else 420.0))
    )


def _warn_delay_threshold(metrics: Dict[str, Any]) -> int:
    route_mode = str(metrics.get("route_mode") or "other")
    multiplier = 1.3 if route_mode in {"light_rail", "subway"} else 1.45
    return int(
        max(
            _service_delay_threshold(metrics) * multiplier,
            480 if route_mode in {"light_rail", "subway"} else 600,
        )
    )


def _is_low_observation(
    *,
    route_mode: str,
    vehicle_count: int,
    trip_update_count: int,
    active_alert_count: int,
) -> bool:
    if route_mode == "bus":
        return vehicle_count <= 1 and trip_update_count < 4 and active_alert_count == 0
    if route_mode in {"commuter_rail", "ferry"}:
        return vehicle_count == 0 and trip_update_count < 2 and active_alert_count == 0
    if route_mode in {"light_rail", "subway"}:
        return vehicle_count == 0 and trip_update_count < 2 and active_alert_count == 0
    return vehicle_count == 0 and trip_update_count < 2


def _has_strong_trip_update_delay_signal(metrics: Dict[str, Any]) -> bool:
    threshold = _service_delay_threshold(metrics)
    if metrics["median_delay_seconds"] < threshold:
        return False
    route_mode = str(metrics.get("route_mode") or "other")
    if route_mode == "bus":
        return metrics["trip_update_count"] >= 4 or metrics[
            "median_delay_seconds"
        ] >= max(720, int(threshold * 1.5))
    if route_mode in {"commuter_rail", "ferry"}:
        return metrics["trip_update_count"] >= 2 and metrics[
            "median_delay_seconds"
        ] >= max(1200, int(threshold * 1.2))
    return metrics["trip_update_count"] >= 2


def _should_suppress_sparse_delay(metrics: Dict[str, Any]) -> bool:
    if metrics["active_alert_count"] >= 2:
        return False
    if not metrics["low_observation"]:
        return False
    threshold = _service_delay_threshold(metrics)
    route_mode = str(metrics.get("route_mode") or "other")
    if route_mode == "bus":
        return metrics["median_delay_seconds"] < max(720, int(threshold * 1.5))
    if route_mode in {"commuter_rail", "ferry"}:
        return metrics["median_delay_seconds"] < max(1200, int(threshold * 1.2))
    return metrics["median_delay_seconds"] < max(480, int(threshold * 1.25))


def _has_alert_corroboration(metrics: Dict[str, Any]) -> bool:
    route_mode = str(metrics.get("route_mode") or "other")
    if route_mode == "bus":
        return (
            metrics["vehicle_count"] >= 2
            or metrics["trip_update_count"] >= 3
            or metrics["high_impact_alert_count"] >= 1
        )
    if route_mode in {"commuter_rail", "ferry"}:
        return (
            metrics["trip_update_count"] >= 2 or metrics["high_impact_alert_count"] >= 1
        )
    return (
        metrics["vehicle_count"] >= 1
        or metrics["trip_update_count"] >= 2
        or metrics["high_impact_alert_count"] >= 1
    )


def _has_delay_corroboration(metrics: Dict[str, Any]) -> bool:
    route_mode = str(metrics.get("route_mode") or "other")
    if route_mode == "bus":
        return metrics["vehicle_count"] >= 2 or metrics["trip_update_count"] >= 4
    if route_mode in {"commuter_rail", "ferry"}:
        return metrics["trip_update_count"] >= 2 and (
            metrics["vehicle_count"] >= 1 or metrics["active_alert_count"] >= 1
        )
    return metrics["vehicle_count"] >= 2 or metrics["trip_update_count"] >= 2


def _warn_riders_corroborated(metrics: Dict[str, Any]) -> bool:
    route_mode = str(metrics.get("route_mode") or "other")
    if route_mode == "bus":
        if metrics["high_impact_alert_count"] >= 1:
            return metrics["vehicle_count"] >= 2 or metrics["trip_update_count"] >= 3
        return (
            metrics["vehicle_count"] >= 3
            and metrics["trip_update_count"] >= 3
            and metrics["median_delay_seconds"] >= _warn_delay_threshold(metrics)
        )
    if route_mode == "commuter_rail":
        if metrics["high_impact_alert_count"] >= 1:
            return metrics["trip_update_count"] >= 2
        return (
            metrics["vehicle_count"] >= 1
            and metrics["trip_update_count"] >= 2
            and metrics["median_delay_seconds"] >= _warn_delay_threshold(metrics)
        )
    if route_mode in {"light_rail", "subway"}:
        if not metrics["scheduled_service_active"] and metrics[
            "median_delay_seconds"
        ] < _warn_delay_threshold(metrics):
            return False
        return (
            metrics["median_delay_seconds"] >= _warn_delay_threshold(metrics)
            or (
                metrics["compressed_headway_share"] >= 0.6
                and metrics["vehicle_count"] >= 3
                and metrics["trip_update_count"] >= 1
            )
            or (
                metrics["high_impact_alert_count"] >= 2
                and metrics["trip_update_count"] >= 1
            )
        )
    return True


def _recommended_action(regime: str, metrics: Dict[str, Any]) -> str:
    if regime == "bunching_onset":
        return "hold"
    if regime == "headway_collapse":
        return "dispatch_relief" if metrics["vehicle_count"] >= 4 else "short_turn"
    if regime in {"terminal_congestion", "stop_dwell_instability"}:
        return "inspect_terminal"
    if regime == "corridor_unstable":
        if (
            metrics["median_delay_seconds"] >= 240
            or metrics["compressed_headway_share"] >= 0.65
        ):
            return "dispatch_relief"
        return "monitor"
    if regime == "service_degraded":
        warn_delay_threshold = _warn_delay_threshold(metrics)
        if (
            (
                metrics["high_impact_alert_count"] >= 1
                and _warn_riders_corroborated(metrics)
            )
            or (
                metrics["active_alert_count"] >= 2
                and _warn_riders_corroborated(metrics)
            )
            or (
                metrics["median_delay_seconds"] >= warn_delay_threshold
                and _warn_riders_corroborated(metrics)
            )
            or (
                metrics["vehicle_count"] >= 3
                and not metrics["low_observation"]
                and metrics["median_delay_seconds"] >= _service_delay_threshold(metrics)
                and _warn_riders_corroborated(metrics)
            )
        ):
            return "warn_riders"
        return "monitor"
    if regime == "feed_incoherent":
        return "mark_feed_degraded"
    return "monitor"


def _hazard_components(metrics: Dict[str, Any]) -> Dict[str, float]:
    scheduled_headway = float(metrics["scheduled_headway_seconds"] or 600)
    delay_factor = clamp(
        float(metrics["median_delay_seconds"])
        / max(1.0, float(_service_delay_threshold(metrics)))
    )
    collapse_factor = (
        clamp(float(metrics["compressed_headway_share"]))
        if _supports_headway_regimes(metrics)
        else 0.0
    )
    terminal_factor = clamp(float(metrics["terminal_backlog_count"]) / 4.0)
    dwell_factor = clamp(float(metrics["dwell_overrun_share"]))
    alert_factor = clamp(float(metrics["active_alert_count"]) / 2.0)
    feed_factor = clamp(
        float(metrics["feed_age_seconds"])
        / max(1.0, float(metrics["stale_after_seconds"]))
    )
    instability_factor = clamp(
        float(metrics["delay_spread_seconds"]) / max(360.0, scheduled_headway)
    )
    return {
        "headway_compression": round(collapse_factor, 4),
        "delay_burden": round(delay_factor, 4),
        "terminal_pressure": round(terminal_factor, 4),
        "dwell_instability": round(dwell_factor, 4),
        "alert_pressure": round(alert_factor, 4),
        "feed_freshness": round(feed_factor, 4),
        "corridor_instability": round(instability_factor, 4),
    }


def _hazard_score(components: Dict[str, float]) -> float:
    weights = {
        "headway_compression": 0.24,
        "delay_burden": 0.2,
        "terminal_pressure": 0.16,
        "dwell_instability": 0.12,
        "alert_pressure": 0.1,
        "feed_freshness": 0.08,
        "corridor_instability": 0.1,
    }
    return clamp(sum(weights[key] * components.get(key, 0.0) for key in weights))


def _build_provenance(
    metrics: Dict[str, Any], components: Dict[str, float]
) -> Tuple[float, Dict[str, Any]]:
    coverage = (float(metrics["position_coverage"]) * 0.55) + (
        float(metrics["trip_update_coverage"]) * 0.45
    )
    freshness = 1.0 - clamp(
        float(metrics["feed_age_seconds"])
        / max(1.0, float(metrics["stale_after_seconds"]))
    )
    signal_agreement = max(components.values(), default=0.0)
    confidence = clamp(
        (0.45 * coverage) + (0.25 * freshness) + (0.30 * signal_agreement)
    )
    if metrics.get("low_observation"):
        confidence *= 0.75
    top_factors = [
        {
            "factor": factor,
            "label": factor.replace("_", " "),
            "score": round(score, 4),
            "weight": round(weight, 4),
            "weighted_score": round(score * weight, 4),
        }
        for factor, weight, score in sorted(
            (
                (factor, weight, components.get(factor, 0.0))
                for factor, weight in {
                    "headway_compression": 0.24,
                    "delay_burden": 0.2,
                    "terminal_pressure": 0.16,
                    "dwell_instability": 0.12,
                    "alert_pressure": 0.1,
                    "feed_freshness": 0.08,
                    "corridor_instability": 0.1,
                }.items()
            ),
            key=lambda row: (-(row[1] * row[2]), -row[2], row[0]),
        )
        if score > 0.0
    ]
    provenance = {
        "feature_coverage": round(coverage, 4),
        "signal_agreement": round(signal_agreement, 4),
        "feed_freshness": round(freshness, 4),
        "metrics": {
            "position_coverage": metrics["position_coverage"],
            "trip_update_coverage": metrics["trip_update_coverage"],
            "feed_age_seconds": metrics["feed_age_seconds"],
        },
        "hazard_components": components,
        "top_factors": top_factors[:4],
    }
    return confidence, provenance


def _build_vehicle_rows(
    catalog: GTFSStaticCatalog,
    vehicles: Sequence[TransitVehicleObservation],
    route_regimes: Sequence[TransitRegimeRecord],
    *,
    agency_key: str,
    event_overlays: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    regimes_by_corridor = {
        str(record.corridor_id or ""): record
        for record in route_regimes
        if record.corridor_id
    }
    rows: List[Dict[str, Any]] = []
    for vehicle in vehicles:
        corridor_id = _corridor_id(agency_key, vehicle.route_id, vehicle.direction_id)
        route_regime = regimes_by_corridor.get(corridor_id)
        matched_event_overlays = (
            list(route_regime.event_overlays)
            if route_regime and route_regime.event_overlays
            else summarize_matching_overlays(
                list(event_overlays),
                route_id=vehicle.route_id,
                corridor_id=corridor_id,
                agency_key=agency_key,
            )
        )
        label = vehicle.vehicle_label or vehicle.vehicle_id
        rows.append(
            TransitVehicleSnapshot(
                entity_id=vehicle.entity_id(),
                label=label,
                vehicle_id=vehicle.vehicle_id,
                agency_key=agency_key,
                corridor_id=corridor_id,
                route_id=vehicle.route_id,
                route_label=catalog.route_label(vehicle.route_id),
                trip_id=vehicle.trip_id,
                direction_id=vehicle.direction_id,
                stop_id=vehicle.stop_id,
                status=vehicle.current_status,
                delay_seconds=vehicle.delay_seconds,
                occupancy_status=vehicle.occupancy_status,
                source=vehicle.source,
                collection_source=vehicle.collection_source,
                corridor_entity_id=route_regime.entity_id if route_regime else None,
                regime=route_regime.to_json() if route_regime else None,
                observation=vehicle.to_json(),
                event_overlays=matched_event_overlays,
            ).to_json()
        )
    rows.sort(
        key=lambda row: (
            -float((row.get("regime") or {}).get("hazard") or 0.0),
            -(abs(int(row.get("delay_seconds") or 0))),
            str(row.get("label") or ""),
        )
    )
    return rows


def _build_recurring_signatures(
    route_regimes: Sequence[TransitRegimeRecord],
) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for regime in route_regimes:
        bucket = buckets.setdefault(
            regime.signature,
            {
                "signature": regime.signature,
                "entity_count": 0,
                "hazard_max": 0.0,
                "regimes": set(),
                "actions": set(),
            },
        )
        bucket["entity_count"] += 1
        bucket["hazard_max"] = max(bucket["hazard_max"], regime.hazard)
        bucket["regimes"].add(regime.regime)
        bucket["actions"].add(regime.action)
    recurring = [
        {
            **bucket,
            "regimes": sorted(bucket["regimes"]),
            "actions": sorted(bucket["actions"]),
        }
        for bucket in buckets.values()
        if bucket["entity_count"] > 1
    ]
    recurring.sort(key=lambda row: (-row["entity_count"], -row["hazard_max"]))
    return recurring


def _build_feed_status(
    bundle: TransitRealtimeBundle, errors: Sequence[str], *, agency_key: str
) -> Dict[str, Any]:
    latest_timestamp_ms = bundle.latest_timestamp_ms()
    return TransitFeedStatus(
        feed_label=bundle.feed_label,
        updated_at=isoformat_ms(latest_timestamp_ms) if latest_timestamp_ms else None,
        agency_key=agency_key,
        vehicle_count=len(bundle.vehicles),
        trip_update_count=len(bundle.trip_updates),
        alert_count=len(bundle.alerts),
        collection_source=bundle.collection_source,
        status="ok" if not errors else "degraded",
    ).to_json()


def _build_health_payload(
    *,
    system_name: str,
    route_rows: Sequence[Dict[str, Any]],
    active_route_rows: Sequence[Dict[str, Any]],
    scheduled_later_route_rows: Sequence[Dict[str, Any]],
    inactive_route_rows: Sequence[Dict[str, Any]],
    vehicle_rows: Sequence[Dict[str, Any]],
    route_regimes: Sequence[TransitRegimeRecord],
    incidents: Sequence[TransitIncidentRecord],
    feed_status: Dict[str, Any],
) -> Dict[str, Any]:
    hazards = [record.hazard for record in route_regimes]
    confidences = [record.confidence for record in route_regimes]
    status = "ok"
    if (
        any(incident.severity == "critical" for incident in incidents)
        or max(hazards or [0.0]) >= 0.85
    ):
        status = "critical"
    elif incidents or max(hazards or [0.0]) >= 0.5:
        status = "warning"
    action_counts: Dict[str, int] = {}
    regime_counts: Dict[str, int] = {}
    for record in route_regimes:
        action_counts[record.action] = action_counts.get(record.action, 0) + 1
        regime_counts[record.regime] = regime_counts.get(record.regime, 0) + 1
    worst = route_regimes[0].to_json() if route_regimes else None
    return {
        "system_name": system_name,
        "generated_at": isoformat_ms(),
        "status": status,
        "line_count": len(active_route_rows),
        "active_line_count": len(active_route_rows),
        "scheduled_later_line_count": len(scheduled_later_route_rows),
        "inactive_line_count": len(inactive_route_rows),
        "visible_line_count": len(route_rows),
        "vehicle_count": len(vehicle_rows),
        "incident_count": len(incidents),
        "critical_incidents": sum(
            1 for incident in incidents if incident.severity == "critical"
        ),
        "avg_hazard": round(sum(hazards) / len(hazards), 4) if hazards else 0.0,
        "avg_confidence": round(sum(confidences) / len(confidences), 4)
        if confidences
        else 0.0,
        "max_hazard": round(max(hazards), 4) if hazards else 0.0,
        "action_counts": action_counts,
        "regime_counts": regime_counts,
        "feed_status": feed_status,
        "worst_corridor": worst,
    }


def _route_activity_status(
    catalog: GTFSStaticCatalog,
    *,
    route_id: str,
    direction_id: Optional[int],
    metrics: Dict[str, Any],
    now_ms: int,
    timezone_name: str,
) -> Tuple[str, str]:
    if metrics["observations_present"]:
        return "active_now", "live_telemetry"
    if metrics["scheduled_service_active"]:
        return "scheduled_later", "scheduled_no_telemetry"
    upcoming_reason = _route_upcoming_service_reason(
        catalog,
        route_id=route_id,
        direction_id=direction_id,
        now_ms=now_ms,
        timezone_name=timezone_name,
    )
    if upcoming_reason:
        return "scheduled_later", upcoming_reason
    return "inactive", "inactive"


def _route_upcoming_service_reason(
    catalog: GTFSStaticCatalog,
    *,
    route_id: str,
    direction_id: Optional[int],
    now_ms: int,
    timezone_name: str,
) -> Optional[str]:
    if route_id in {"network", "unassigned"} or not catalog.calendar:
        return None
    try:
        zone = ZoneInfo(timezone_name)
    except Exception:
        zone = timezone.utc
    local_dt = datetime.fromtimestamp(now_ms / 1000.0, tz=zone)
    current_date = local_dt.date()
    seconds_of_day = (local_dt.hour * 3600) + (local_dt.minute * 60) + local_dt.second

    current_window = catalog.route_service_window(
        route_id, direction_id=direction_id, service_date=current_date
    )
    if current_window:
        start_seconds, _ = current_window
        if seconds_of_day < start_seconds:
            return "service_starts_later"

    next_date = current_date + timedelta(days=1)
    next_window = catalog.route_service_window(
        route_id, direction_id=direction_id, service_date=next_date
    )
    if next_window:
        return "returns_next_service_day"
    return None


def _severity_for_action(action: str, hazard: float) -> str:
    if action in {"dispatch_relief", "short_turn"}:
        return "critical" if hazard >= 0.82 else "warning"
    if action in {"inspect_terminal", "hold", "mark_feed_degraded"}:
        return "warning"
    if action == "warn_riders":
        return "warning" if hazard >= 0.7 else "info"
    return "info"


def _incident_summary(record: TransitRegimeRecord, metrics: Dict[str, Any]) -> str:
    headline = {
        "bunching_onset": f"{record.label} is showing early bunching.",
        "corridor_unstable": f"{record.label} is running irregularly.",
        "headway_collapse": f"{record.label} shows severe bunching with likely service gaps.",
        "service_degraded": f"{record.label} has corroborated service disruption.",
        "terminal_congestion": f"{record.label} is queueing at the terminal.",
        "stop_dwell_instability": f"{record.label} is losing time at stops.",
        "feed_incoherent": f"{record.label} has degraded telemetry.",
    }.get(record.regime, f"{record.label} requires operational review.")
    median_delay = int(metrics.get("median_delay_seconds") or 0)
    evidence_parts = [
        f"median delay {median_delay}s"
        if median_delay
        else "no measured delay burden",
        f"{int(metrics['vehicle_count'])} vehicles",
    ]
    if int(metrics.get("trip_update_count") or 0) > 0:
        evidence_parts.append(f"{int(metrics['trip_update_count'])} trip updates")
    if int(metrics.get("high_impact_alert_count") or 0) > 0:
        evidence_parts.append(
            f"{int(metrics['high_impact_alert_count'])} high-impact service alerts"
        )
    elif int(metrics.get("active_alert_count") or 0) > 0:
        evidence_parts.append(f"{int(metrics['active_alert_count'])} service alerts")
    return (
        f"{headline} Evidence: {', '.join(evidence_parts)}. "
        f"Recommended action: {transit_action_label(record.action)}."
    )


def _recommended_text(action: str) -> str:
    mapping = {
        "monitor": "Keep the line under watch and confirm service stabilizes.",
        "hold": "Hold the trailing trip or vehicle to reopen headways before bunching hardens.",
        "short_turn": "Short-turn one trip to rebuild spacing and close the service gap.",
        "dispatch_relief": "Dispatch relief service or direct field supervision to restore spacing.",
        "inspect_terminal": "Inspect the terminal or stop cluster for queueing, dispatch, or boarding issues.",
        "warn_riders": "Publish a rider advisory and align customer information with observed service.",
        "mark_feed_degraded": "Mark the feed degraded before operators mistake telemetry gaps for service recovery.",
    }
    return mapping.get(action, "Inspect the affected service.")


def _summarize_route_alerts(
    alerts: Sequence[TransitAlertObservation],
) -> Dict[str, int]:
    seen_signatures: set[str] = set()
    total_alert_count = 0
    service_alert_count = 0
    facility_alert_count = 0
    high_impact_alert_count = 0
    for alert in alerts:
        signature = _alert_signature(alert)
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        total_alert_count += 1
        if _alert_is_service_impacting(alert):
            service_alert_count += 1
            if _alert_is_high_impact(alert):
                high_impact_alert_count += 1
        else:
            facility_alert_count += 1
    return {
        "total_alert_count": total_alert_count,
        "service_alert_count": service_alert_count,
        "facility_alert_count": facility_alert_count,
        "high_impact_alert_count": high_impact_alert_count,
    }


def _alert_signature(alert: TransitAlertObservation) -> str:
    header = " ".join(str(alert.header_text or "").lower().split())
    description = " ".join(str(alert.description_text or "").lower().split())
    return "|".join(
        [
            str(alert.effect or "").upper(),
            str(alert.cause or "").upper(),
            header,
            description[:120],
        ]
    )


def _alert_is_service_impacting(alert: TransitAlertObservation) -> bool:
    effect = str(alert.effect or "").upper()
    text = _alert_text(alert)
    if effect in SERVICE_IMPACT_ALERT_EFFECTS:
        return True
    if effect == "ACCESSIBILITY_ISSUE":
        return False
    if any(keyword in text for keyword in FACILITY_ALERT_KEYWORDS):
        return False
    return any(keyword in text for keyword in SERVICE_ALERT_KEYWORDS)


def _alert_is_high_impact(alert: TransitAlertObservation) -> bool:
    effect = str(alert.effect or "").upper()
    text = _alert_text(alert)
    if effect in HIGH_IMPACT_ALERT_EFFECTS:
        return True
    return any(
        keyword in text
        for keyword in (
            "delay",
            "behind schedule",
            "shuttle",
            "replace service",
            "detour",
            "no service",
            "terminate",
        )
    )


def _alert_text(alert: TransitAlertObservation) -> str:
    return " ".join(
        part
        for part in [
            " ".join(str(alert.header_text or "").lower().split()),
            " ".join(str(alert.description_text or "").lower().split()),
        ]
        if part
    )


def _quantile(values: Sequence[int], quantile: float) -> int:
    if not values:
        return 0
    if quantile <= 0:
        return min(values)
    if quantile >= 1:
        return max(values)
    rows = sorted(values)
    position = (len(rows) - 1) * quantile
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return rows[lower]
    lower_value = rows[lower]
    upper_value = rows[upper]
    fraction = position - lower
    return int(round(lower_value + ((upper_value - lower_value) * fraction)))


def _derive_delay_from_schedule(
    catalog: GTFSStaticCatalog,
    *,
    trip_id: str,
    service_date: Optional[str],
    timezone_name: str,
    stop_sequence: Optional[int],
    stop_id: Optional[str],
    scheduled_event: str,
    realtime_unix: int,
) -> Optional[int]:
    scheduled_unix = catalog.scheduled_epoch_seconds(
        trip_id,
        service_date=service_date,
        timezone_name=timezone_name,
        stop_sequence=stop_sequence,
        stop_id=stop_id,
        event=scheduled_event,
    )
    if scheduled_unix is None:
        return None
    return int(realtime_unix - scheduled_unix)


def _corridor_label(
    catalog: GTFSStaticCatalog,
    route_id: str,
    direction_id: Optional[int],
    vehicles: Sequence[TransitVehicleObservation],
    trip_updates: Sequence[TransitTripUpdateObservation],
) -> str:
    base = catalog.route_label(route_id)
    trip_ids = [row.trip_id for row in [*vehicles, *trip_updates] if row.trip_id]
    for trip_id in trip_ids:
        headsign = catalog.trip_headsign(trip_id)
        if headsign:
            return f"{base} to {headsign}"
    if direction_id is not None:
        return f"{base} direction {direction_id}"
    return base


def _corridor_entity_id(route_id: str | None, direction_id: Optional[int]) -> str:
    return f"route:{route_id or 'unassigned'}:{direction_id if direction_id is not None else 'all'}"


def _corridor_id(
    agency_key: str | None, route_id: str | None, direction_id: Optional[int]
) -> str:
    return f"corridor:{agency_key or default_transit_agency_key()}:{route_id or 'unassigned'}:{direction_id if direction_id is not None else 'all'}"


def _default_feed_timezone(system_name: str) -> str:
    if str(system_name or "").strip().upper() == "MBTA":
        return "America/New_York"
    return "UTC"


def _default_current_feed(env_name: str, default_path: Path) -> Optional[str]:
    explicit = os.getenv(env_name)
    if explicit:
        return explicit
    return str(default_path) if default_path.exists() else None


def _gtfs_load_options() -> Dict[str, bool]:
    lightweight = _env_flag("TRANSIT_GTFS_LIGHTWEIGHT", default=False)
    return {
        "include_stops": _env_flag("TRANSIT_GTFS_LOAD_STOPS", default=not lightweight),
        "include_stop_times": _env_flag(
            "TRANSIT_GTFS_LOAD_STOP_TIMES", default=not lightweight
        ),
        "include_calendar": _env_flag(
            "TRANSIT_GTFS_LOAD_CALENDAR", default=not lightweight
        ),
        "include_shapes": _env_flag("TRANSIT_GTFS_LOAD_SHAPES", default=not lightweight),
    }


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _local_mtime_ns(source: str) -> Optional[int]:
    if source.startswith(("http://", "https://")):
        return None
    path = Path(source)
    if not path.exists():
        return None
    return path.stat().st_mtime_ns
