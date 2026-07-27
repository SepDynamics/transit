"""Versioned, replay-friendly stop prediction evidence records."""

from __future__ import annotations

from typing import Any, Dict, Sequence

from scripts.transit.transit_types import (
    GTFSStaticCatalog,
    TransitStopTimeUpdate,
    TransitTripUpdateObservation,
)

PREDICTION_EVIDENCE_SCHEMA_VERSION = "sentinel.prediction_evidence.v1"


def build_prediction_evidence(
    catalog: GTFSStaticCatalog,
    trip_updates: Sequence[TransitTripUpdateObservation],
    *,
    agency_key: str,
    snapshot_timestamp_ms: int,
    feed_timestamp_ms: int | None,
    timezone_name: str,
) -> Dict[str, Any]:
    """Normalize filtered trip updates into deterministic per-stop events.

    Absolute predicted times published by GTFS-RT are retained as-is.  When a
    feed publishes only a delay, a predicted time is derived from the static
    schedule and explicitly labeled ``schedule_plus_delay``.  Skipped stops
    remain present even when neither event contains a time.  Trip descriptors
    are retained separately so a cancellation without stop updates is not
    lost, and the trip relationship is repeated on stop events for portable
    replay records.
    """

    events: list[Dict[str, Any]] = []
    trip_descriptors = [_trip_descriptor(update) for update in trip_updates]
    for update in trip_updates:
        for stop_update in update.stop_time_updates:
            events.append(
                _prediction_event(
                    catalog,
                    update,
                    stop_update,
                    timezone_name=timezone_name,
                )
            )
    events.sort(key=_event_sort_key)
    trip_descriptors.sort(key=_trip_descriptor_sort_key)
    arrivals_with_time = sum(
        1 for event in events if event.get("arrival_time_unix") is not None
    )
    departures_with_time = sum(
        1 for event in events if event.get("departure_time_unix") is not None
    )
    skipped_stops = sum(
        1
        for event in events
        if str(event.get("schedule_relationship") or "").upper() == "SKIPPED"
    )
    return {
        "schema_version": PREDICTION_EVIDENCE_SCHEMA_VERSION,
        "agency_key": agency_key,
        "snapshot_timestamp_ms": int(snapshot_timestamp_ms),
        "feed_timestamp_ms": int(feed_timestamp_ms)
        if feed_timestamp_ms is not None
        else None,
        "trip_update_count": len(trip_updates),
        "trip_descriptor_count": len(trip_descriptors),
        "event_count": len(events),
        "coverage": {
            "arrival_time_count": arrivals_with_time,
            "departure_time_count": departures_with_time,
            "skipped_stop_count": skipped_stops,
        },
        "trip_descriptors": trip_descriptors,
        "events": events,
    }


def _trip_descriptor(update: TransitTripUpdateObservation) -> Dict[str, Any]:
    return {
        "route_id": update.route_id,
        "trip_id": update.trip_id,
        "direction_id": update.direction_id,
        "vehicle_id": update.vehicle_id,
        "service_date": update.service_date,
        "start_time": update.start_time,
        "schedule_relationship": update.schedule_relationship,
        "trip_update_timestamp_ms": update.timestamp_ms,
        "source": update.source,
        "collection_source": update.collection_source,
        "trace_id": update.trace_id,
    }


def _prediction_event(
    catalog: GTFSStaticCatalog,
    update: TransitTripUpdateObservation,
    stop_update: TransitStopTimeUpdate,
    *,
    timezone_name: str,
) -> Dict[str, Any]:
    scheduled_arrival = catalog.scheduled_epoch_seconds(
        update.trip_id,
        service_date=update.service_date,
        timezone_name=timezone_name,
        stop_sequence=stop_update.stop_sequence,
        stop_id=stop_update.stop_id,
        event="arrival",
    )
    scheduled_departure = catalog.scheduled_epoch_seconds(
        update.trip_id,
        service_date=update.service_date,
        timezone_name=timezone_name,
        stop_sequence=stop_update.stop_sequence,
        stop_id=stop_update.stop_id,
        event="departure",
    )
    arrival_time, arrival_source = _predicted_time(
        published_time=stop_update.arrival_time_unix,
        scheduled_time=scheduled_arrival,
        delay_seconds=stop_update.arrival_delay_seconds,
    )
    departure_time, departure_source = _predicted_time(
        published_time=stop_update.departure_time_unix,
        scheduled_time=scheduled_departure,
        delay_seconds=stop_update.departure_delay_seconds,
    )
    return {
        "route_id": update.route_id,
        "trip_id": update.trip_id,
        "direction_id": update.direction_id,
        "vehicle_id": update.vehicle_id,
        "service_date": update.service_date,
        "stop_id": stop_update.stop_id,
        "stop_sequence": stop_update.stop_sequence,
        "scheduled_arrival_time_unix": scheduled_arrival,
        "arrival_time_unix": arrival_time,
        "arrival_time_source": arrival_source,
        "arrival_delay_seconds": stop_update.arrival_delay_seconds,
        "scheduled_departure_time_unix": scheduled_departure,
        "departure_time_unix": departure_time,
        "departure_time_source": departure_source,
        "departure_delay_seconds": stop_update.departure_delay_seconds,
        "schedule_relationship": stop_update.schedule_relationship,
        "trip_schedule_relationship": update.schedule_relationship,
        "trip_delay_seconds": update.delay_seconds,
        "trip_update_timestamp_ms": update.timestamp_ms,
        "source": update.source,
        "collection_source": update.collection_source,
        "trace_id": update.trace_id,
    }


def _predicted_time(
    *,
    published_time: int | None,
    scheduled_time: int | None,
    delay_seconds: int | None,
) -> tuple[int | None, str | None]:
    if published_time is not None:
        return int(published_time), "gtfs_rt_time"
    if scheduled_time is not None and delay_seconds is not None:
        return int(scheduled_time) + int(delay_seconds), "schedule_plus_delay"
    return None, None


def _event_sort_key(
    event: Dict[str, Any],
) -> tuple[str, str, int, str, int, int, str]:
    sequence = event.get("stop_sequence")
    return (
        str(event.get("route_id") or ""),
        str(event.get("trip_id") or ""),
        int(sequence) if sequence is not None else 2**31 - 1,
        str(event.get("stop_id") or ""),
        _optional_int_sort_value(event.get("arrival_time_unix")),
        _optional_int_sort_value(event.get("departure_time_unix")),
        str(event.get("schedule_relationship") or ""),
    )


def _trip_descriptor_sort_key(
    descriptor: Dict[str, Any],
) -> tuple[str, str, str, int, str, str]:
    return (
        str(descriptor.get("route_id") or ""),
        str(descriptor.get("trip_id") or ""),
        str(descriptor.get("service_date") or ""),
        _optional_int_sort_value(descriptor.get("trip_update_timestamp_ms")),
        str(descriptor.get("schedule_relationship") or ""),
        str(descriptor.get("vehicle_id") or ""),
    )


def _optional_int_sort_value(value: Any) -> int:
    return int(value) if value is not None else 2**63 - 1
