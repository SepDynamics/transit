"""Load and normalize GTFS and GTFS-RT feeds for Transit Sentinel."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional
from zipfile import ZipFile

import requests

from scripts.transit.transit_types import (
    GTFSCalendarService,
    GTFSRoute,
    GTFSShapePoint,
    GTFSStaticCatalog,
    GTFSStop,
    GTFSStopTime,
    GTFSTrip,
    TransitAlertObservation,
    TransitRealtimeBundle,
    TransitStopTimeUpdate,
    TransitTripUpdateObservation,
    TransitVehicleObservation,
)


def load_gtfs_catalog(
    source: str | Path | bytes, *, feed_label: Optional[str] = None
) -> GTFSStaticCatalog:
    data = _read_resource(source)
    rows_by_file: Dict[str, List[Dict[str, str]]] = {}
    if _looks_like_zip_bytes(data):
        with ZipFile(io.BytesIO(data)) as archive:
            for name in archive.namelist():
                if not name.endswith(".txt"):
                    continue
                with archive.open(name, "r") as handle:
                    rows_by_file[Path(name).name] = list(
                        csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8-sig"))
                    )
    else:
        path = _coerce_path(source)
        if path and path.is_dir():
            for name in (
                "routes.txt",
                "trips.txt",
                "stops.txt",
                "stop_times.txt",
                "calendar.txt",
                "shapes.txt",
            ):
                file_path = path / name
                if file_path.exists():
                    rows_by_file[name] = list(
                        csv.DictReader(
                            file_path.read_text(encoding="utf-8-sig").splitlines()
                        )
                    )
        else:
            raise ValueError(
                "GTFS static feed must be a zip archive, directory, or bytes payload"
            )

    catalog = GTFSStaticCatalog(feed_label=feed_label or _default_feed_label(source))
    for row in rows_by_file.get("routes.txt", []):
        route = GTFSRoute(
            route_id=str(row.get("route_id") or "").strip(),
            route_short_name=str(row.get("route_short_name") or "").strip(),
            route_long_name=str(row.get("route_long_name") or "").strip(),
            route_desc=str(row.get("route_desc") or "").strip(),
            route_type=_optional_int(row.get("route_type")),
            agency_id=_string_or_none(row.get("agency_id")),
        )
        if route.route_id:
            catalog.routes[route.route_id] = route

    for row in rows_by_file.get("trips.txt", []):
        trip = GTFSTrip(
            trip_id=str(row.get("trip_id") or "").strip(),
            route_id=str(row.get("route_id") or "").strip(),
            service_id=str(row.get("service_id") or "").strip(),
            trip_headsign=str(row.get("trip_headsign") or "").strip(),
            direction_id=_optional_int(row.get("direction_id")),
            shape_id=_string_or_none(row.get("shape_id")),
            block_id=_string_or_none(row.get("block_id")),
        )
        if trip.trip_id:
            catalog.trips[trip.trip_id] = trip

    for row in rows_by_file.get("stops.txt", []):
        stop = GTFSStop(
            stop_id=str(row.get("stop_id") or "").strip(),
            stop_name=str(row.get("stop_name") or row.get("stop_code") or "").strip(),
            stop_lat=_optional_float(row.get("stop_lat")),
            stop_lon=_optional_float(row.get("stop_lon")),
            parent_station=_string_or_none(row.get("parent_station")),
            location_type=_optional_int(row.get("location_type")),
        )
        if stop.stop_id:
            catalog.stops[stop.stop_id] = stop

    for row in rows_by_file.get("stop_times.txt", []):
        trip_id = str(row.get("trip_id") or "").strip()
        if not trip_id:
            continue
        stop_time = GTFSStopTime(
            trip_id=trip_id,
            stop_id=str(row.get("stop_id") or "").strip(),
            stop_sequence=int(row.get("stop_sequence") or 0),
            arrival_time=_string_or_none(row.get("arrival_time")),
            departure_time=_string_or_none(row.get("departure_time")),
        )
        catalog.stop_times_by_trip.setdefault(trip_id, []).append(stop_time)
    for trip_id, rows in catalog.stop_times_by_trip.items():
        rows.sort(key=lambda row: row.stop_sequence)
        catalog.stop_times_by_trip[trip_id] = rows

    for row in rows_by_file.get("calendar.txt", []):
        service = GTFSCalendarService(
            service_id=str(row.get("service_id") or "").strip(),
            start_date=_string_or_none(row.get("start_date")),
            end_date=_string_or_none(row.get("end_date")),
            monday=int(row.get("monday") or 0),
            tuesday=int(row.get("tuesday") or 0),
            wednesday=int(row.get("wednesday") or 0),
            thursday=int(row.get("thursday") or 0),
            friday=int(row.get("friday") or 0),
            saturday=int(row.get("saturday") or 0),
            sunday=int(row.get("sunday") or 0),
        )
        if service.service_id:
            catalog.calendar[service.service_id] = service

    for row in rows_by_file.get("shapes.txt", []):
        shape_id = str(row.get("shape_id") or "").strip()
        if not shape_id:
            continue
        point = GTFSShapePoint(
            shape_id=shape_id,
            shape_pt_sequence=int(row.get("shape_pt_sequence") or 0),
            shape_pt_lat=float(row.get("shape_pt_lat") or 0.0),
            shape_pt_lon=float(row.get("shape_pt_lon") or 0.0),
        )
        catalog.shapes.setdefault(shape_id, []).append(point)
    for shape_id, points in catalog.shapes.items():
        points.sort(key=lambda row: row.shape_pt_sequence)
        catalog.shapes[shape_id] = points

    return catalog


def load_gtfs_realtime_resource(
    source: str | Path | bytes | Mapping[str, Any],
    *,
    feed_label: str,
    collection_source: str,
    payload_type: str,
    trace_id: Optional[str] = None,
) -> TransitRealtimeBundle:
    if isinstance(source, Mapping):
        payload: Any = source
    else:
        raw = _read_resource(source)
        payload = _parse_realtime_payload(raw)
    bundle = normalize_gtfs_realtime_payload(
        payload,
        feed_label=feed_label,
        collection_source=collection_source,
        payload_type=payload_type,
        trace_id=trace_id,
    )
    return bundle


def normalize_gtfs_realtime_payload(
    payload: Mapping[str, Any],
    *,
    feed_label: str,
    collection_source: str,
    payload_type: str,
    trace_id: Optional[str] = None,
) -> TransitRealtimeBundle:
    header = dict(_mapping(payload).get("header") or {})
    feed_timestamp_ms = _optional_int(_field(header, "timestamp", "feed_timestamp"))
    if feed_timestamp_ms is not None and feed_timestamp_ms < 10_000_000_000:
        feed_timestamp_ms *= 1000

    vehicles: List[TransitVehicleObservation] = []
    trip_updates: List[TransitTripUpdateObservation] = []
    alerts: List[TransitAlertObservation] = []
    for index, raw_entity in enumerate(_mapping(payload).get("entity") or []):
        entity = _mapping(raw_entity)
        entity_id = str(_field(entity, "id") or f"{payload_type}-{index}")
        if _field(entity, "vehicle"):
            vehicles.append(
                _normalize_vehicle_entity(
                    entity_id,
                    _mapping(_field(entity, "vehicle")),
                    feed_timestamp_ms,
                    collection_source,
                    trace_id,
                )
            )
        if _field(entity, "trip_update", "tripUpdate"):
            trip_updates.append(
                _normalize_trip_update_entity(
                    entity_id,
                    _mapping(_field(entity, "trip_update", "tripUpdate")),
                    feed_timestamp_ms,
                    collection_source,
                    trace_id,
                )
            )
        if _field(entity, "alert"):
            alerts.append(
                _normalize_alert_entity(
                    entity_id,
                    _mapping(_field(entity, "alert")),
                    feed_timestamp_ms,
                    collection_source,
                    trace_id,
                )
            )

    if payload_type == "vehicle_positions":
        trip_updates = []
        alerts = []
    elif payload_type == "trip_updates":
        vehicles = []
        alerts = []
    elif payload_type == "alerts":
        vehicles = []
        trip_updates = []

    return TransitRealtimeBundle(
        feed_label=feed_label,
        feed_timestamp_ms=feed_timestamp_ms,
        vehicles=vehicles,
        trip_updates=trip_updates,
        alerts=alerts,
        source="live",
        collection_source=collection_source,
        trace_id=trace_id,
    )


def merge_realtime_bundles(
    feed_label: str, *bundles: TransitRealtimeBundle
) -> TransitRealtimeBundle:
    vehicles: List[TransitVehicleObservation] = []
    trip_updates: List[TransitTripUpdateObservation] = []
    alerts: List[TransitAlertObservation] = []
    timestamps = [
        bundle.feed_timestamp_ms
        for bundle in bundles
        if bundle.feed_timestamp_ms is not None
    ]
    sources = [
        bundle.collection_source for bundle in bundles if bundle.collection_source
    ]
    trace_id = next((bundle.trace_id for bundle in bundles if bundle.trace_id), None)
    for bundle in bundles:
        vehicles.extend(bundle.vehicles)
        trip_updates.extend(bundle.trip_updates)
        alerts.extend(bundle.alerts)
    return TransitRealtimeBundle(
        feed_label=feed_label,
        feed_timestamp_ms=max(timestamps) if timestamps else None,
        vehicles=vehicles,
        trip_updates=trip_updates,
        alerts=alerts,
        source="live",
        collection_source="+".join(sorted(set(sources))) if sources else "gtfs_rt",
        trace_id=trace_id,
    )


def _normalize_vehicle_entity(
    entity_id: str,
    payload: Mapping[str, Any],
    feed_timestamp_ms: Optional[int],
    collection_source: str,
    trace_id: Optional[str],
) -> TransitVehicleObservation:
    trip = _mapping(_field(payload, "trip"))
    vehicle = _mapping(_field(payload, "vehicle"))
    position = _mapping(_field(payload, "position"))
    timestamp_ms = _optional_int(_field(payload, "timestamp")) or feed_timestamp_ms or 0
    if timestamp_ms and timestamp_ms < 10_000_000_000:
        timestamp_ms *= 1000
    vehicle_id = str(_field(vehicle, "id") or entity_id)
    return TransitVehicleObservation(
        timestamp_ms=timestamp_ms,
        route_id=_string_or_none(_field(trip, "route_id", "routeId")),
        trip_id=_string_or_none(_field(trip, "trip_id", "tripId")),
        vehicle_id=vehicle_id,
        vehicle_label=_string_or_none(_field(vehicle, "label")),
        direction_id=_optional_int(_field(trip, "direction_id", "directionId")),
        service_date=_string_or_none(_field(trip, "start_date", "startDate")),
        start_time=_string_or_none(_field(trip, "start_time", "startTime")),
        stop_id=_string_or_none(_field(payload, "stop_id", "stopId")),
        current_status=_string_or_none(
            _field(payload, "current_status", "currentStatus")
        ),
        current_stop_sequence=_optional_int(
            _field(payload, "current_stop_sequence", "currentStopSequence")
        ),
        occupancy_status=_string_or_none(
            _field(payload, "occupancy_status", "occupancyStatus")
        ),
        latitude=_optional_float(_field(position, "latitude", "lat")),
        longitude=_optional_float(_field(position, "longitude", "lon")),
        bearing=_optional_float(_field(position, "bearing")),
        speed_mps=_optional_float(_field(position, "speed")),
        delay_seconds=_optional_int(
            _field(payload, "delay", "current_delay", "currentDelay")
        ),
        congestion_level=_string_or_none(
            _field(position, "congestion_level", "congestionLevel")
        ),
        source="live",
        collection_source=collection_source,
        trace_id=trace_id,
    )


def _normalize_trip_update_entity(
    entity_id: str,
    payload: Mapping[str, Any],
    feed_timestamp_ms: Optional[int],
    collection_source: str,
    trace_id: Optional[str],
) -> TransitTripUpdateObservation:
    trip = _mapping(_field(payload, "trip"))
    vehicle = _mapping(_field(payload, "vehicle"))
    timestamp_ms = _optional_int(_field(payload, "timestamp")) or feed_timestamp_ms or 0
    if timestamp_ms and timestamp_ms < 10_000_000_000:
        timestamp_ms *= 1000
    stop_time_updates: List[TransitStopTimeUpdate] = []
    delays: List[int] = []
    for raw_update in _field(payload, "stop_time_update", "stopTimeUpdate") or []:
        update = _mapping(raw_update)
        arrival = _mapping(_field(update, "arrival"))
        departure = _mapping(_field(update, "departure"))
        arrival_delay = _optional_int(_field(arrival, "delay"))
        departure_delay = _optional_int(_field(departure, "delay"))
        if arrival_delay is not None:
            delays.append(arrival_delay)
        if departure_delay is not None:
            delays.append(departure_delay)
        stop_time_updates.append(
            TransitStopTimeUpdate(
                stop_id=_string_or_none(_field(update, "stop_id", "stopId")),
                stop_sequence=_optional_int(
                    _field(update, "stop_sequence", "stopSequence")
                ),
                arrival_time_unix=_optional_int(_field(arrival, "time")),
                departure_time_unix=_optional_int(_field(departure, "time")),
                arrival_delay_seconds=arrival_delay,
                departure_delay_seconds=departure_delay,
                schedule_relationship=_string_or_none(
                    _field(update, "schedule_relationship", "scheduleRelationship")
                ),
            )
        )
    return TransitTripUpdateObservation(
        timestamp_ms=timestamp_ms,
        route_id=_string_or_none(_field(trip, "route_id", "routeId")),
        trip_id=str(_field(trip, "trip_id", "tripId") or entity_id),
        vehicle_id=_string_or_none(_field(vehicle, "id")),
        direction_id=_optional_int(_field(trip, "direction_id", "directionId")),
        service_date=_string_or_none(_field(trip, "start_date", "startDate")),
        start_time=_string_or_none(_field(trip, "start_time", "startTime")),
        delay_seconds=max(delays, key=lambda value: abs(value)) if delays else None,
        stop_time_updates=stop_time_updates,
        source="live",
        collection_source=collection_source,
        trace_id=trace_id,
    )


def _normalize_alert_entity(
    entity_id: str,
    payload: Mapping[str, Any],
    feed_timestamp_ms: Optional[int],
    collection_source: str,
    trace_id: Optional[str],
) -> TransitAlertObservation:
    informed_entities = [
        _mapping(row)
        for row in _field(payload, "informed_entity", "informedEntity") or []
    ]
    route_ids = sorted(
        {
            str(_field(row, "route_id", "routeId"))
            for row in informed_entities
            if _field(row, "route_id", "routeId")
        }
    )
    stop_ids = sorted(
        {
            str(_field(row, "stop_id", "stopId"))
            for row in informed_entities
            if _field(row, "stop_id", "stopId")
        }
    )
    trip_ids = sorted(
        {
            str(_field(row, "trip_id", "tripId"))
            for row in informed_entities
            if _field(row, "trip_id", "tripId")
        }
    )
    return TransitAlertObservation(
        alert_id=entity_id,
        timestamp_ms=feed_timestamp_ms or 0,
        effect=_string_or_none(_field(payload, "effect")),
        cause=_string_or_none(_field(payload, "cause")),
        header_text=_translation_text(_field(payload, "header_text", "headerText")),
        description_text=_translation_text(
            _field(payload, "description_text", "descriptionText")
        ),
        route_ids=route_ids,
        stop_ids=stop_ids,
        trip_ids=trip_ids,
        source="live",
        collection_source=collection_source,
        trace_id=trace_id,
    )


def _read_resource(source: str | Path | bytes) -> bytes:
    if isinstance(source, bytes):
        return source
    if isinstance(source, Path):
        path = source
    else:
        value = str(source)
        if value.startswith(("http://", "https://")):
            response = requests.get(value, timeout=30)
            response.raise_for_status()
            return response.content
        path = Path(value)
    if path.is_dir():
        return b""
    return path.read_bytes()


def _coerce_path(source: str | Path | bytes) -> Optional[Path]:
    if isinstance(source, bytes):
        return None
    return source if isinstance(source, Path) else Path(str(source))


def _parse_realtime_payload(raw: bytes) -> Mapping[str, Any]:
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        try:
            from google.protobuf.json_format import MessageToDict  # type: ignore
            from google.transit import gtfs_realtime_pb2  # type: ignore
        except (
            ImportError
        ) as exc:  # pragma: no cover - exercised when bindings are unavailable
            raise ValueError(
                "GTFS-RT protobuf parsing requires google.transit.gtfs_realtime_pb2 or JSON input"
            ) from exc

        message = gtfs_realtime_pb2.FeedMessage()
        message.ParseFromString(raw)
        return MessageToDict(message, preserving_proto_field_name=True)


def _field(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
        camel = _snake_to_camel(key)
        if camel in mapping:
            return mapping[camel]
    return None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _translation_text(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return value or None
    if isinstance(value, Mapping):
        translations = value.get("translation")
        if isinstance(translations, Iterable):
            for row in translations:
                if isinstance(row, Mapping) and row.get("text"):
                    return str(row["text"])
    return None


def _snake_to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _string_or_none(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return str(value)


def _default_feed_label(source: str | Path | bytes) -> str:
    if isinstance(source, bytes):
        return "transit-feed"
    path = _coerce_path(source)
    return path.stem or path.name or "transit-feed" if path else "transit-feed"


def _looks_like_zip_bytes(data: bytes) -> bool:
    return bool(data[:4] == b"PK\x03\x04")
