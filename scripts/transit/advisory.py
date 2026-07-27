"""Deterministic, explainable alternative-service advisories.

This module deliberately separates rider advice from operational actions.  It
compiles a small routing graph from static GTFS, overlays per-stop GTFS-RT
predictions, and only publishes an alternative when the disrupted service and
the proposed replacement both have sufficiently fresh, corroborated evidence.
"""

from __future__ import annotations

import gzip
import io
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from scripts.transit.transit_types import (
    GTFSStaticCatalog,
    GTFSStop,
    TransitRealtimeBundle,
    normalize_trip_schedule_relationship,
)


DISRUPTED_REGIMES = frozenset(
    {
        "bunching_onset",
        "corridor_unstable",
        "headway_collapse",
        "service_degraded",
        "terminal_congestion",
        "stop_dwell_instability",
        "terminal_blocked",
    }
)
TOPOLOGY_ARTIFACT_VERSION = 1
PREDICTION_EVIDENCE_SCHEMA_VERSION = "sentinel.prediction_evidence.v1"
ROUTABLE_TRIP_SCHEDULE_RELATIONSHIPS = frozenset(
    {"SCHEDULED", "ADDED", "UNSCHEDULED"}
)
NON_RUNNING_TRIP_SCHEDULE_RELATIONSHIPS = frozenset({"CANCELED", "DELETED"})
UNSUPPORTED_TRIP_SCHEDULE_RELATIONSHIPS = frozenset(
    {"REPLACEMENT", "DUPLICATED", "NEW"}
)


@dataclass(frozen=True)
class ExplicitTransfer:
    """A normalized, directed transfer from GTFS ``transfers.txt`` or policy."""

    from_stop_id: str
    to_stop_id: str
    minimum_transfer_seconds: int = 0
    bidirectional: bool = False
    permitted: bool = True


@dataclass(frozen=True)
class RideEdge:
    from_stop_id: str
    to_stop_id: str
    route_id: str
    direction_id: Optional[int]
    trip_id: str
    from_stop_sequence: int
    to_stop_sequence: int
    scheduled_travel_seconds: Optional[int]


@dataclass(frozen=True)
class TransferEdge:
    from_stop_id: str
    to_stop_id: str
    minimum_transfer_seconds: int
    distance_meters: Optional[float]
    source: str


@dataclass(frozen=True)
class TripStop:
    stop_id: str
    stop_sequence: int


@dataclass(frozen=True)
class TripPath:
    trip_id: str
    route_id: str
    direction_id: Optional[int]
    stops: Tuple[TripStop, ...]


@dataclass
class TransitTopology:
    """Static topology used by the bounded advisory search."""

    feed_label: str
    stops: Mapping[str, GTFSStop]
    route_labels: Mapping[str, str]
    route_types: Mapping[str, Optional[int]]
    ride_edges: Tuple[RideEdge, ...]
    transfer_edges: Tuple[TransferEdge, ...]
    trip_paths: Mapping[str, TripPath]
    trips_by_stop: Mapping[str, Tuple[str, ...]]
    transfers_from_stop: Mapping[str, Tuple[TransferEdge, ...]]
    transfers_to_stop: Mapping[str, Tuple[TransferEdge, ...]]

    def to_dict(self) -> Dict[str, Any]:
        """Return a stable artifact payload suitable for offline compilation."""

        return {
            "schema_version": TOPOLOGY_ARTIFACT_VERSION,
            "feed_label": self.feed_label,
            "routes": [
                {
                    "route_id": route_id,
                    "label": self.route_labels.get(route_id, route_id),
                    "route_type": self.route_types.get(route_id),
                }
                for route_id in sorted(self.route_labels)
            ],
            "stops": [asdict(self.stops[stop_id]) for stop_id in sorted(self.stops)],
            "ride_edges": [asdict(edge) for edge in self.ride_edges],
            "transfer_edges": [asdict(edge) for edge in self.transfer_edges],
            **self._pattern_payload(),
        }

    def _pattern_payload(self) -> Dict[str, Any]:
        signatures = sorted(
            {
                (
                    path.route_id,
                    path.direction_id,
                    path.stops,
                )
                for path in self.trip_paths.values()
            },
            key=lambda signature: (
                signature[0],
                signature[1] is None,
                -1 if signature[1] is None else signature[1],
                tuple((stop.stop_id, stop.stop_sequence) for stop in signature[2]),
            ),
        )
        pattern_ids = {signature: f"pattern-{index:06d}" for index, signature in enumerate(signatures, 1)}
        patterns = [
            {
                "pattern_id": pattern_ids[signature],
                "route_id": signature[0],
                "direction_id": signature[1],
                "stops": [
                    {"stop_id": stop.stop_id, "stop_sequence": stop.stop_sequence}
                    for stop in signature[2]
                ],
            }
            for signature in signatures
        ]
        trips = [
            {
                "trip_id": path.trip_id,
                "pattern_id": pattern_ids[
                    (
                        path.route_id,
                        path.direction_id,
                        path.stops,
                    )
                ],
            }
            for _, path in sorted(self.trip_paths.items())
        ]
        return {"patterns": patterns, "trips": trips}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TransitTopology":
        """Load and validate a topology artifact without the source GTFS feed."""

        version = int(payload.get("schema_version") or 0)
        if version != TOPOLOGY_ARTIFACT_VERSION:
            raise ValueError(f"unsupported transit topology schema version: {version}")
        stops = {
            str(row["stop_id"]): GTFSStop(**dict(row))
            for row in payload.get("stops", ())
            if isinstance(row, Mapping) and row.get("stop_id")
        }
        route_labels: Dict[str, str] = {}
        route_types: Dict[str, Optional[int]] = {}
        for row in payload.get("routes", ()):
            if not isinstance(row, Mapping) or not row.get("route_id"):
                continue
            route_id = str(row["route_id"])
            route_labels[route_id] = str(row.get("label") or route_id)
            route_type = row.get("route_type")
            route_types[route_id] = int(route_type) if route_type is not None else None
        ride_edges = tuple(RideEdge(**dict(row)) for row in payload.get("ride_edges", ()))
        transfer_edges = tuple(TransferEdge(**dict(row)) for row in payload.get("transfer_edges", ()))
        patterns: Dict[str, Tuple[str, Optional[int], Tuple[TripStop, ...]]] = {}
        for row in payload.get("patterns", ()):
            if not isinstance(row, Mapping) or not row.get("pattern_id"):
                continue
            raw_direction = row.get("direction_id")
            patterns[str(row["pattern_id"])] = (
                str(row["route_id"]),
                int(raw_direction) if raw_direction is not None else None,
                tuple(TripStop(**dict(stop)) for stop in row.get("stops", ())),
            )
        trip_paths: Dict[str, TripPath] = {}
        for row in payload.get("trips", ()):
            if not isinstance(row, Mapping) or not row.get("trip_id"):
                continue
            pattern = patterns.get(str(row.get("pattern_id") or ""))
            if pattern is None:
                raise ValueError(f"unknown pattern_id for trip {row['trip_id']}")
            path = TripPath(
                trip_id=str(row["trip_id"]),
                route_id=pattern[0],
                direction_id=pattern[1],
                stops=pattern[2],
            )
            trip_paths[path.trip_id] = path
        return build_transit_topology(
            feed_label=str(payload.get("feed_label") or "compiled_gtfs"),
            stops=stops,
            route_labels=route_labels,
            route_types=route_types,
            ride_edges=ride_edges,
            transfer_edges=transfer_edges,
            trip_paths=trip_paths,
        )


def save_transit_topology(
    topology: TransitTopology,
    destination: str | Path,
    *,
    metadata: Optional[Mapping[str, Any]] = None,
) -> None:
    """Write deterministic ``.json`` or ``.json.gz`` for runtime loading."""

    payload = topology.to_dict()
    if metadata is not None:
        payload["metadata"] = dict(metadata)
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination_path.parent,
            prefix=f".{destination_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as raw_handle:
            temporary_name = raw_handle.name
            if destination_path.suffix == ".gz":
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw_handle, mtime=0) as compressed:
                    with io.TextIOWrapper(compressed, encoding="utf-8") as text_handle:
                        json.dump(payload, text_handle, sort_keys=True, separators=(",", ":"))
                        text_handle.write("\n")
            else:
                with io.TextIOWrapper(raw_handle, encoding="utf-8") as text_handle:
                    json.dump(payload, text_handle, sort_keys=True, separators=(",", ":"))
                    text_handle.write("\n")
        os.replace(temporary_name, destination_path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def load_transit_topology(source: str | Path) -> TransitTopology:
    """Load a topology previously written by :func:`save_transit_topology`."""

    source_path = Path(source)
    if source_path.suffix == ".gz":
        with gzip.open(source_path, mode="rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    else:
        with source_path.open(mode="r", encoding="utf-8") as handle:
            payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise ValueError("transit topology artifact must be a JSON object")
    return TransitTopology.from_dict(payload)


def build_transit_topology(
    *,
    feed_label: str,
    stops: Mapping[str, GTFSStop],
    route_labels: Mapping[str, str],
    route_types: Mapping[str, Optional[int]],
    ride_edges: Iterable[RideEdge],
    transfer_edges: Iterable[TransferEdge],
    trip_paths: Mapping[str, TripPath],
) -> TransitTopology:
    """Build deterministic lookup indexes from compiler-ready topology rows."""

    ordered_rides = tuple(
        sorted(
            ride_edges,
            key=lambda edge: (
                edge.trip_id,
                edge.from_stop_sequence,
                edge.to_stop_sequence,
                edge.from_stop_id,
                edge.to_stop_id,
            ),
        )
    )
    ordered_transfers = tuple(
        sorted(
            transfer_edges,
            key=lambda edge: (edge.from_stop_id, edge.to_stop_id, edge.source),
        )
    )
    ordered_paths = dict(sorted(trip_paths.items()))
    trips_by_stop: Dict[str, set[str]] = {}
    for trip_id, path in ordered_paths.items():
        for stop in path.stops:
            trips_by_stop.setdefault(stop.stop_id, set()).add(trip_id)
    transfers_from: Dict[str, List[TransferEdge]] = {}
    transfers_to: Dict[str, List[TransferEdge]] = {}
    for edge in ordered_transfers:
        transfers_from.setdefault(edge.from_stop_id, []).append(edge)
        transfers_to.setdefault(edge.to_stop_id, []).append(edge)
    return TransitTopology(
        feed_label=feed_label,
        stops=dict(sorted(stops.items())),
        route_labels=dict(sorted(route_labels.items())),
        route_types=dict(sorted(route_types.items())),
        ride_edges=ordered_rides,
        transfer_edges=ordered_transfers,
        trip_paths=ordered_paths,
        trips_by_stop={key: tuple(sorted(value)) for key, value in sorted(trips_by_stop.items())},
        transfers_from_stop={key: tuple(value) for key, value in sorted(transfers_from.items())},
        transfers_to_stop={key: tuple(value) for key, value in sorted(transfers_to.items())},
    )


def compile_transit_topology(
    catalog: GTFSStaticCatalog,
    *,
    explicit_transfers: Iterable[ExplicitTransfer] = (),
    max_nearby_walk_meters: float = 250.0,
    walking_speed_mps: float = 1.2,
    station_transfer_floor_seconds: int = 60,
) -> TransitTopology:
    """Compile ordered ride edges and bounded walking/transfer links.

    Same-stop route changes, platforms sharing a parent station, explicit
    transfers, and geographically nearby served stops are represented.  The
    compiler is deterministic: output ordering does not depend on dictionary
    insertion order.
    """

    if max_nearby_walk_meters < 0:
        raise ValueError("max_nearby_walk_meters must be non-negative")
    if walking_speed_mps <= 0:
        raise ValueError("walking_speed_mps must be positive")

    ride_edge_stats: Dict[Tuple[str, Optional[int], str, str], Dict[str, Any]] = {}
    trip_paths: Dict[str, TripPath] = {}
    shared_stop_patterns: Dict[
        Tuple[str, Optional[int], Tuple[Tuple[str, int], ...]], Tuple[TripStop, ...]
    ] = {}
    routes_by_stop: Dict[str, set[str]] = {}

    for trip_id in sorted(catalog.trips):
        trip = catalog.trips[trip_id]
        rows = sorted(
            catalog.stop_times_by_trip.get(trip_id, ()),
            key=lambda row: row.stop_sequence,
        )
        usable_rows = [row for row in rows if row.stop_id in catalog.stops]
        if not usable_rows:
            continue
        pattern_key = (
            trip.route_id,
            trip.direction_id,
            tuple((row.stop_id, row.stop_sequence) for row in usable_rows),
        )
        shared_stops = shared_stop_patterns.setdefault(
            pattern_key,
            tuple(TripStop(row.stop_id, row.stop_sequence) for row in usable_rows),
        )
        trip_paths[trip_id] = TripPath(
            trip_id=trip_id,
            route_id=trip.route_id,
            direction_id=trip.direction_id,
            stops=shared_stops,
        )
        for row in usable_rows:
            routes_by_stop.setdefault(row.stop_id, set()).add(trip.route_id)
        for first, second in zip(usable_rows, usable_rows[1:]):
            departure = first.departure_seconds()
            if departure is None:
                departure = first.arrival_seconds()
            arrival = second.arrival_seconds()
            if arrival is None:
                arrival = second.departure_seconds()
            travel_seconds = None
            if departure is not None and arrival is not None and arrival >= departure:
                travel_seconds = arrival - departure
            edge_key = (trip.route_id, trip.direction_id, first.stop_id, second.stop_id)
            stats = ride_edge_stats.setdefault(
                edge_key,
                {
                    "trip_id": trip_id,
                    "from_stop_sequence": first.stop_sequence,
                    "to_stop_sequence": second.stop_sequence,
                    "duration_total": 0,
                    "duration_count": 0,
                },
            )
            if trip_id < stats["trip_id"]:
                stats["trip_id"] = trip_id
                stats["from_stop_sequence"] = first.stop_sequence
                stats["to_stop_sequence"] = second.stop_sequence
            if travel_seconds is not None:
                stats["duration_total"] += travel_seconds
                stats["duration_count"] += 1

    ride_edges = []
    for (route_id, direction_id, from_stop_id, to_stop_id), stats in sorted(
        ride_edge_stats.items(),
        key=lambda item: (
            item[0][0],
            item[0][1] is None,
            -1 if item[0][1] is None else item[0][1],
            item[0][2],
            item[0][3],
        ),
    ):
        duration = None
        if stats["duration_count"]:
            duration = int(round(stats["duration_total"] / stats["duration_count"]))
        ride_edges.append(
            RideEdge(
                from_stop_id=from_stop_id,
                to_stop_id=to_stop_id,
                route_id=route_id,
                direction_id=direction_id,
                trip_id=stats["trip_id"],
                from_stop_sequence=stats["from_stop_sequence"],
                to_stop_sequence=stats["to_stop_sequence"],
                scheduled_travel_seconds=duration,
            )
        )

    served_stop_ids = sorted(routes_by_stop)
    transfer_by_pair: Dict[Tuple[str, str], TransferEdge] = {}
    blocked_transfer_pairs: set[Tuple[str, str]] = set()

    def block_transfer(from_stop_id: str, to_stop_id: str) -> None:
        pair = (from_stop_id, to_stop_id)
        blocked_transfer_pairs.add(pair)
        transfer_by_pair.pop(pair, None)

    def add_transfer(edge: TransferEdge) -> None:
        if edge.from_stop_id not in catalog.stops or edge.to_stop_id not in catalog.stops:
            return
        pair = (edge.from_stop_id, edge.to_stop_id)
        if pair in blocked_transfer_pairs:
            return
        current = transfer_by_pair.get(pair)
        priority = {
            "inferred_nearby_stop": 0,
            "inferred_shared_station": 1,
            "inferred_shared_stop": 2,
            "agency_defined": 3,
            "configured": 4,
        }
        if current is None or priority[edge.source] > priority[current.source]:
            transfer_by_pair[pair] = edge
        elif (
            priority[edge.source] == priority[current.source]
            and edge.minimum_transfer_seconds < current.minimum_transfer_seconds
        ):
            transfer_by_pair[pair] = edge

    for stop_id in served_stop_ids:
        if len(routes_by_stop.get(stop_id, ())) >= 2:
            add_transfer(TransferEdge(stop_id, stop_id, 0, 0.0, "inferred_shared_stop"))

    platforms_by_parent: Dict[str, List[str]] = {}
    for stop_id in served_stop_ids:
        parent = catalog.stops[stop_id].parent_station
        if parent:
            platforms_by_parent.setdefault(parent, []).append(stop_id)
    for platform_ids in platforms_by_parent.values():
        for from_stop_id in sorted(platform_ids):
            for to_stop_id in sorted(platform_ids):
                if from_stop_id == to_stop_id:
                    continue
                distance = _stop_distance_meters(catalog.stops[from_stop_id], catalog.stops[to_stop_id])
                walk_seconds = station_transfer_floor_seconds
                if distance is not None:
                    walk_seconds = max(walk_seconds, int(math.ceil(distance / walking_speed_mps)))
                add_transfer(
                    TransferEdge(
                        from_stop_id,
                        to_stop_id,
                        max(0, walk_seconds),
                        distance,
                        "inferred_shared_station",
                    )
                )

    if max_nearby_walk_meters > 0:
        for index, from_stop_id in enumerate(served_stop_ids):
            from_stop = catalog.stops[from_stop_id]
            for to_stop_id in served_stop_ids[index + 1 :]:
                to_stop = catalog.stops[to_stop_id]
                distance = _stop_distance_meters(from_stop, to_stop)
                if distance is None or distance > max_nearby_walk_meters:
                    continue
                walk_seconds = int(math.ceil(distance / walking_speed_mps))
                add_transfer(
                    TransferEdge(from_stop_id, to_stop_id, walk_seconds, distance, "inferred_nearby_stop")
                )
                add_transfer(
                    TransferEdge(to_stop_id, from_stop_id, walk_seconds, distance, "inferred_nearby_stop")
                )

    for transfer in catalog.transfers:
        if transfer.transfer_type == 3:
            block_transfer(transfer.from_stop_id, transfer.to_stop_id)
            continue
        from_stop = catalog.stops.get(transfer.from_stop_id)
        to_stop = catalog.stops.get(transfer.to_stop_id)
        distance = _stop_distance_meters(
            from_stop,
            to_stop,
        )
        supplied_minimum_seconds = 0
        if transfer.transfer_type == 2 and transfer.min_transfer_time is not None:
            supplied_minimum_seconds = max(0, int(transfer.min_transfer_time))
        transfer_seconds = max(
            supplied_minimum_seconds,
            _defensible_transfer_seconds(
                from_stop_id=transfer.from_stop_id,
                to_stop_id=transfer.to_stop_id,
                distance_meters=distance,
                walking_speed_mps=walking_speed_mps,
                station_transfer_floor_seconds=station_transfer_floor_seconds,
                shared_parent_station=_share_parent_station(from_stop, to_stop),
            ),
        )
        add_transfer(
            TransferEdge(
                transfer.from_stop_id,
                transfer.to_stop_id,
                transfer_seconds,
                distance,
                "agency_defined",
            )
        )

    for transfer in explicit_transfers:
        if not transfer.permitted:
            block_transfer(transfer.from_stop_id, transfer.to_stop_id)
            if transfer.bidirectional:
                block_transfer(transfer.to_stop_id, transfer.from_stop_id)
            continue
        from_stop = catalog.stops.get(transfer.from_stop_id)
        to_stop = catalog.stops.get(transfer.to_stop_id)
        distance = _stop_distance_meters(from_stop, to_stop)
        edge = TransferEdge(
            transfer.from_stop_id,
            transfer.to_stop_id,
            max(
                0,
                int(transfer.minimum_transfer_seconds),
                _defensible_transfer_seconds(
                    from_stop_id=transfer.from_stop_id,
                    to_stop_id=transfer.to_stop_id,
                    distance_meters=distance,
                    walking_speed_mps=walking_speed_mps,
                    station_transfer_floor_seconds=station_transfer_floor_seconds,
                    shared_parent_station=_share_parent_station(from_stop, to_stop),
                ),
            ),
            distance,
            "configured",
        )
        add_transfer(edge)
        if transfer.bidirectional:
            reverse_seconds = max(
                0,
                int(transfer.minimum_transfer_seconds),
                _defensible_transfer_seconds(
                    from_stop_id=transfer.to_stop_id,
                    to_stop_id=transfer.from_stop_id,
                    distance_meters=edge.distance_meters,
                    walking_speed_mps=walking_speed_mps,
                    station_transfer_floor_seconds=station_transfer_floor_seconds,
                    shared_parent_station=_share_parent_station(to_stop, from_stop),
                ),
            )
            add_transfer(
                TransferEdge(
                    edge.to_stop_id,
                    edge.from_stop_id,
                    reverse_seconds,
                    edge.distance_meters,
                    edge.source,
                )
            )

    transfer_edges = tuple(
        sorted(
            transfer_by_pair.values(),
            key=lambda edge: (edge.from_stop_id, edge.to_stop_id, edge.source),
        )
    )
    ride_edges.sort(
        key=lambda edge: (
            edge.trip_id,
            edge.from_stop_sequence,
            edge.to_stop_sequence,
            edge.from_stop_id,
            edge.to_stop_id,
        )
    )
    return build_transit_topology(
        feed_label=catalog.feed_label,
        stops=dict(catalog.stops),
        route_labels={route_id: catalog.route_label(route_id) for route_id in catalog.routes},
        route_types={route_id: route.route_type for route_id, route in catalog.routes.items()},
        ride_edges=tuple(ride_edges),
        transfer_edges=transfer_edges,
        trip_paths=trip_paths,
    )


@dataclass(frozen=True)
class StopPrediction:
    trip_id: str
    route_id: str
    stop_id: str
    stop_sequence: int
    arrival_time_ms: int
    departure_time_ms: int
    observed_at_ms: int
    direction_id: Optional[int] = None
    schedule_relationship: Optional[str] = None
    trip_schedule_relationship: Optional[str] = None
    arrival_time_source: Optional[str] = None
    departure_time_source: Optional[str] = None
    collection_source: Optional[str] = None


@dataclass
class RealtimePredictionIndex:
    """Freshest usable prediction for each trip/stop occurrence."""

    by_trip: Mapping[str, Tuple[StopPrediction, ...]]
    skipped_trip_stops: frozenset[Tuple[str, str, int]] = frozenset()
    no_data_trip_stops: frozenset[Tuple[str, str, int]] = frozenset()
    blocked_trip_ids: frozenset[str] = frozenset()

    @classmethod
    def from_bundle(
        cls,
        topology: TransitTopology,
        bundle: TransitRealtimeBundle,
        *,
        catalog: Optional[GTFSStaticCatalog] = None,
        timezone_name: str = "UTC",
    ) -> "RealtimePredictionIndex":
        records: Dict[Tuple[str, str, int], Tuple[int, str, Optional[StopPrediction]]] = {}
        trip_relationships: Dict[str, Tuple[int, str]] = {}
        for trip_update in bundle.trip_updates:
            _set_trip_relationship(
                trip_relationships,
                trip_update.trip_id,
                trip_update.timestamp_ms or bundle.feed_timestamp_ms or 0,
                trip_update.schedule_relationship,
            )
        blocked_trip_ids = _blocked_trip_ids(trip_relationships)
        for trip_update in bundle.trip_updates:
            trip_id = trip_update.trip_id
            if trip_id in blocked_trip_ids:
                continue
            trip_relationship = normalize_trip_schedule_relationship(
                trip_update.schedule_relationship
            )
            if (
                trip_relationship is not None
                and not _trip_relationship_is_routable(trip_relationship)
            ):
                continue
            path = topology.trip_paths.get(trip_id)
            route_id = trip_update.route_id or (path.route_id if path else None)
            direction_id = trip_update.direction_id
            if direction_id is None and path is not None:
                direction_id = path.direction_id
            if not route_id:
                continue
            observed_at_ms = trip_update.timestamp_ms or bundle.feed_timestamp_ms or 0
            for update in trip_update.stop_time_updates:
                static_stop = _find_trip_path_stop(
                    path,
                    stop_sequence=update.stop_sequence,
                    stop_id=update.stop_id,
                )
                if path is not None and static_stop is None:
                    continue
                stop_id = update.stop_id or (static_stop.stop_id if static_stop else None)
                stop_sequence = update.stop_sequence
                if stop_sequence is None and static_stop is not None:
                    stop_sequence = static_stop.stop_sequence
                if not stop_id or stop_sequence is None:
                    continue
                key = (trip_id, stop_id, stop_sequence)
                relationship = str(update.schedule_relationship or "").upper() or None
                if relationship in {"SKIPPED", "NO_DATA"}:
                    _set_prediction_record(records, key, observed_at_ms, relationship, None)
                    continue
                arrival_ms = _epoch_milliseconds(update.arrival_time_unix)
                departure_ms = _epoch_milliseconds(update.departure_time_unix)
                arrival_source = "gtfs_rt_time" if arrival_ms is not None else None
                departure_source = "gtfs_rt_time" if departure_ms is not None else None
                if catalog is not None and arrival_ms is None and update.arrival_delay_seconds is not None:
                    scheduled = catalog.scheduled_epoch_seconds(
                        trip_id,
                        service_date=trip_update.service_date,
                        timezone_name=timezone_name,
                        stop_sequence=stop_sequence,
                        stop_id=stop_id,
                        event="arrival",
                    )
                    if scheduled is not None:
                        arrival_ms = (scheduled + update.arrival_delay_seconds) * 1000
                        arrival_source = "schedule_plus_delay"
                if catalog is not None and departure_ms is None and update.departure_delay_seconds is not None:
                    scheduled = catalog.scheduled_epoch_seconds(
                        trip_id,
                        service_date=trip_update.service_date,
                        timezone_name=timezone_name,
                        stop_sequence=stop_sequence,
                        stop_id=stop_id,
                        event="departure",
                    )
                    if scheduled is not None:
                        departure_ms = (scheduled + update.departure_delay_seconds) * 1000
                        departure_source = "schedule_plus_delay"
                if arrival_ms is None:
                    arrival_ms = departure_ms
                    arrival_source = departure_source
                if departure_ms is None:
                    departure_ms = arrival_ms
                    departure_source = arrival_source
                if arrival_ms is None or departure_ms is None:
                    _set_prediction_record(records, key, observed_at_ms, "UNAVAILABLE", None)
                    continue
                prediction = StopPrediction(
                    trip_id=trip_id,
                    route_id=route_id,
                    stop_id=stop_id,
                    stop_sequence=stop_sequence,
                    arrival_time_ms=arrival_ms,
                    departure_time_ms=departure_ms,
                    observed_at_ms=observed_at_ms,
                    direction_id=direction_id,
                    schedule_relationship=relationship,
                    trip_schedule_relationship=normalize_trip_schedule_relationship(
                        trip_update.schedule_relationship
                    ),
                    arrival_time_source=arrival_source,
                    departure_time_source=departure_source,
                    collection_source=trip_update.collection_source,
                )
                _set_prediction_record(records, key, observed_at_ms, "PREDICTION", prediction)

        return cls._from_records(records, blocked_trip_ids=blocked_trip_ids)

    @classmethod
    def from_evidence(
        cls,
        prediction_evidence: Mapping[str, Any],
        topology: TransitTopology,
    ) -> "RealtimePredictionIndex":
        """Build an index from the compact prediction record stored in Valkey."""

        nested = prediction_evidence.get("prediction_evidence")
        evidence = nested if isinstance(nested, Mapping) else prediction_evidence
        schema_version = str(evidence.get("schema_version") or "")
        if schema_version != PREDICTION_EVIDENCE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported prediction evidence schema version: {schema_version or 'missing'}"
            )
        records: Dict[Tuple[str, str, int], Tuple[int, str, Optional[StopPrediction]]] = {}
        fallback_observed_ms = _optional_int_value(evidence.get("feed_timestamp_ms"))
        if fallback_observed_ms is None:
            fallback_observed_ms = _optional_int_value(evidence.get("snapshot_timestamp_ms")) or 0
        events = evidence.get("events", ())
        if not isinstance(events, list):
            raise ValueError("prediction evidence events must be a list")
        trip_descriptors = evidence.get("trip_descriptors", [])
        if not isinstance(trip_descriptors, list):
            raise ValueError("prediction evidence trip_descriptors must be a list")
        trip_relationships: Dict[str, Tuple[int, str]] = {}
        for descriptor in trip_descriptors:
            if not isinstance(descriptor, Mapping):
                continue
            trip_id = str(descriptor.get("trip_id") or "")
            observed_at_ms = _optional_int_value(
                descriptor.get("trip_update_timestamp_ms")
            )
            _set_trip_relationship(
                trip_relationships,
                trip_id,
                fallback_observed_ms if observed_at_ms is None else observed_at_ms,
                descriptor.get("schedule_relationship"),
            )
        for event in events:
            if not isinstance(event, Mapping):
                continue
            if "trip_schedule_relationship" not in event:
                continue
            observed_at_ms = _optional_int_value(event.get("trip_update_timestamp_ms"))
            _set_trip_relationship(
                trip_relationships,
                str(event.get("trip_id") or ""),
                fallback_observed_ms if observed_at_ms is None else observed_at_ms,
                event.get("trip_schedule_relationship"),
            )
        blocked_trip_ids = _blocked_trip_ids(trip_relationships)
        for event in events:
            if not isinstance(event, Mapping):
                continue
            trip_id = str(event.get("trip_id") or "")
            if trip_id in blocked_trip_ids:
                continue
            if "trip_schedule_relationship" in event:
                trip_relationship = normalize_trip_schedule_relationship(
                    event.get("trip_schedule_relationship")
                )
                if (
                    trip_relationship is not None
                    and not _trip_relationship_is_routable(trip_relationship)
                ):
                    continue
            path = topology.trip_paths.get(trip_id)
            static_stop = _find_trip_path_stop(
                path,
                stop_sequence=_optional_int_value(event.get("stop_sequence")),
                stop_id=str(event.get("stop_id") or "") or None,
            )
            if path is not None and static_stop is None:
                continue
            stop_id = str(event.get("stop_id") or (static_stop.stop_id if static_stop else ""))
            stop_sequence = _optional_int_value(event.get("stop_sequence"))
            if stop_sequence is None and static_stop is not None:
                stop_sequence = static_stop.stop_sequence
            if not trip_id or not stop_id or stop_sequence is None:
                continue
            route_id = str(event.get("route_id") or (path.route_id if path else ""))
            if not route_id:
                continue
            direction_id = _optional_int_value(event.get("direction_id"))
            if direction_id is None and path is not None:
                direction_id = path.direction_id
            observed_at_ms = _optional_int_value(event.get("trip_update_timestamp_ms"))
            if observed_at_ms is None:
                observed_at_ms = fallback_observed_ms
            key = (trip_id, stop_id, stop_sequence)
            relationship = str(event.get("schedule_relationship") or "").upper() or None
            if relationship in {"SKIPPED", "NO_DATA"}:
                _set_prediction_record(records, key, observed_at_ms, relationship, None)
                continue
            arrival_ms = _epoch_milliseconds(_optional_int_value(event.get("arrival_time_unix")))
            departure_ms = _epoch_milliseconds(_optional_int_value(event.get("departure_time_unix")))
            arrival_source = str(event.get("arrival_time_source") or "") or None
            departure_source = str(event.get("departure_time_source") or "") or None
            if arrival_ms is None:
                arrival_ms = departure_ms
                arrival_source = departure_source
            if departure_ms is None:
                departure_ms = arrival_ms
                departure_source = arrival_source
            if arrival_ms is None or departure_ms is None:
                _set_prediction_record(records, key, observed_at_ms, "UNAVAILABLE", None)
                continue
            prediction = StopPrediction(
                trip_id=trip_id,
                route_id=route_id,
                stop_id=stop_id,
                stop_sequence=stop_sequence,
                arrival_time_ms=arrival_ms,
                departure_time_ms=departure_ms,
                observed_at_ms=observed_at_ms,
                direction_id=direction_id,
                schedule_relationship=relationship,
                trip_schedule_relationship=normalize_trip_schedule_relationship(
                    event.get("trip_schedule_relationship")
                ),
                arrival_time_source=arrival_source,
                departure_time_source=departure_source,
                collection_source=str(event.get("collection_source") or "") or None,
            )
            _set_prediction_record(records, key, observed_at_ms, "PREDICTION", prediction)

        return cls._from_records(records, blocked_trip_ids=blocked_trip_ids)

    @classmethod
    def _from_records(
        cls,
        records: Mapping[
            Tuple[str, str, int], Tuple[int, str, Optional[StopPrediction]]
        ],
        *,
        blocked_trip_ids: frozenset[str] = frozenset(),
    ) -> "RealtimePredictionIndex":
        predictions: List[StopPrediction] = []
        skipped: set[Tuple[str, str, int]] = set()
        no_data: set[Tuple[str, str, int]] = set()
        for key, (_, status, prediction) in records.items():
            if key[0] in blocked_trip_ids:
                continue
            if status == "SKIPPED":
                skipped.add(key)
            elif status in {"NO_DATA", "UNAVAILABLE"}:
                no_data.add(key)
            elif prediction is not None:
                predictions.append(prediction)

        by_trip: Dict[str, List[StopPrediction]] = {}
        for prediction in predictions:
            by_trip.setdefault(prediction.trip_id, []).append(prediction)
        return cls(
            by_trip={
                trip_id: tuple(sorted(rows, key=lambda row: row.stop_sequence))
                for trip_id, rows in sorted(by_trip.items())
            },
            skipped_trip_stops=frozenset(skipped),
            no_data_trip_stops=frozenset(no_data),
            blocked_trip_ids=blocked_trip_ids,
        )


@dataclass(frozen=True)
class RouteHealth:
    route_id: str
    disruption_score: float
    confidence: float
    observed_at_ms: int
    regime: str = ""
    direction_id: Optional[int] = None


RouteHealthLookup = Mapping[str | Tuple[str, Optional[int]], RouteHealth]


@dataclass(frozen=True)
class AdvisoryRequest:
    origin_stop_id: str
    destination_stop_id: str
    disrupted_route_id: str
    requested_at_ms: int
    direction_id: Optional[int] = None


@dataclass(frozen=True)
class AdvisoryPolicy:
    disruption_threshold: float = 0.65
    disruption_confidence_threshold: float = 0.60
    maximum_alternative_disruption_score: float = 0.40
    minimum_alternative_health_confidence: float = 0.50
    max_health_age_seconds: int = 300
    max_prediction_age_seconds: int = 120
    future_observation_tolerance_seconds: int = 15
    minimum_realtime_coverage: float = 0.75
    minimum_benefit_seconds: int = 300
    maximum_initial_wait_seconds: int = 1800
    maximum_journey_seconds: int = 7200
    maximum_walking_seconds: int = 900
    maximum_walking_meters: float = 750.0
    minimum_transfer_seconds: int = 90
    health_penalty_seconds: int = 600
    transfer_penalty_seconds: int = 180
    advisory_ttl_seconds: int = 300
    minimum_publish_confidence: float = 0.60
    max_advisories: int = 3


@dataclass(frozen=True)
class AdvisoryLeg:
    kind: str
    from_stop_id: str
    to_stop_id: str
    departure_time_ms: Optional[int]
    arrival_time_ms: Optional[int]
    duration_seconds: int
    route_id: Optional[str] = None
    trip_id: Optional[str] = None
    direction_id: Optional[int] = None
    realtime_coverage: Optional[float] = None
    transfer_source: Optional[str] = None


@dataclass(frozen=True)
class AdvisoryEvidence:
    kind: str
    details: Mapping[str, Any]


@dataclass(frozen=True)
class AlternativeAdvisory:
    disrupted_route_id: str
    origin_stop_id: str
    destination_stop_id: str
    route_ids: Tuple[str, ...]
    estimated_arrival_time_ms: int
    baseline_arrival_time_ms: int
    expected_time_saved_seconds: int
    total_walking_seconds: int
    total_walking_meters: Optional[float]
    total_transfer_seconds: int
    confidence: float
    confidence_label: str
    expires_at_ms: int
    summary: str
    explanation: str
    legs: Tuple[AdvisoryLeg, ...]
    evidence: Tuple[AdvisoryEvidence, ...]


@dataclass(frozen=True)
class AdvisoryDecision:
    status: str
    generated_at_ms: int
    advisories: Tuple[AlternativeAdvisory, ...] = ()
    suppression_reasons: Tuple[str, ...] = ()
    evaluated_candidate_count: int = 0
    baseline_arrival_time_ms: Optional[int] = None

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _RideSegment:
    trip_id: str
    route_id: str
    direction_id: Optional[int]
    from_stop_id: str
    to_stop_id: str
    departure_time_ms: int
    arrival_time_ms: int
    observed_at_ms: int
    realtime_coverage: float
    departure_time_source: Optional[str]
    arrival_time_source: Optional[str]
    collection_source: Optional[str]


@dataclass(frozen=True)
class _Candidate:
    ride_segments: Tuple[_RideSegment, ...]
    access_edge: Optional[TransferEdge]
    connection_edge: Optional[TransferEdge]
    egress_edge: Optional[TransferEdge]
    arrival_time_ms: int
    ranking_time_ms: int
    walk_seconds: int
    walk_meters: Optional[float]
    transfer_seconds: int


class AlternativeServiceAdvisor:
    """Find direct or one-transfer alternatives with strict publication gates."""

    def __init__(
        self,
        topology: TransitTopology,
        *,
        catalog: Optional[GTFSStaticCatalog] = None,
        policy: AdvisoryPolicy = AdvisoryPolicy(),
        timezone_name: str = "UTC",
    ) -> None:
        self.topology = topology
        self.catalog = catalog
        self.policy = policy
        self.timezone_name = timezone_name

    def recommend(
        self,
        request: AdvisoryRequest,
        realtime: TransitRealtimeBundle | RealtimePredictionIndex | Mapping[str, Any],
        route_health: RouteHealthLookup,
    ) -> AdvisoryDecision:
        now_ms = request.requested_at_ms
        validation_error = self._validate_request(request)
        if validation_error:
            return self._suppressed(now_ms, validation_error)

        disrupted_health = self._resolve_health(
            route_health,
            request.disrupted_route_id,
            request.direction_id,
        )
        trigger_reason = self._disruption_trigger_failure(disrupted_health, now_ms)
        if trigger_reason:
            return self._suppressed(now_ms, trigger_reason)

        if isinstance(realtime, RealtimePredictionIndex):
            prediction_index = realtime
        elif isinstance(realtime, TransitRealtimeBundle):
            prediction_index = RealtimePredictionIndex.from_bundle(
                self.topology,
                realtime,
                catalog=self.catalog,
                timezone_name=self.timezone_name,
            )
        else:
            try:
                prediction_index = RealtimePredictionIndex.from_evidence(
                    realtime,
                    self.topology,
                )
            except (TypeError, ValueError):
                return self._suppressed(now_ms, "invalid_realtime_prediction_evidence")
        prediction_trips = self._prediction_trips(prediction_index, now_ms)
        if not prediction_trips:
            return self._suppressed(now_ms, "no_fresh_realtime_predictions")

        baseline = self._best_segment(
            prediction_trips,
            route_id=request.disrupted_route_id,
            from_stop_id=request.origin_stop_id,
            to_stop_id=request.destination_stop_id,
            earliest_departure_ms=now_ms,
            direction_id=request.direction_id,
        )
        if baseline is None:
            return self._suppressed(now_ms, "no_reliable_disrupted_route_baseline")

        candidates = self._candidate_itineraries(request, prediction_trips, route_health, now_ms)
        evaluated_count = len(candidates)
        qualifying: List[AlternativeAdvisory] = []
        for candidate in candidates:
            benefit_seconds = int((baseline.arrival_time_ms - candidate.arrival_time_ms) / 1000)
            if benefit_seconds < self.policy.minimum_benefit_seconds:
                continue
            advisory = self._build_advisory(
                request,
                baseline,
                candidate,
                route_health,
                disrupted_health,
                benefit_seconds,
            )
            if advisory.confidence < self.policy.minimum_publish_confidence:
                continue
            if advisory.expires_at_ms <= now_ms:
                continue
            qualifying.append(advisory)

        if not qualifying:
            return AdvisoryDecision(
                status="suppressed",
                generated_at_ms=now_ms,
                suppression_reasons=("no_materially_better_reliable_alternative",),
                evaluated_candidate_count=evaluated_count,
                baseline_arrival_time_ms=baseline.arrival_time_ms,
            )

        qualifying.sort(
            key=lambda advisory: (
                next(
                    candidate.ranking_time_ms
                    for candidate in candidates
                    if self._candidate_key(candidate) == self._advisory_key(advisory)
                ),
                -advisory.confidence,
                advisory.route_ids,
            )
        )
        unique: List[AlternativeAdvisory] = []
        seen_route_sequences: set[Tuple[str, ...]] = set()
        for advisory in qualifying:
            if advisory.route_ids in seen_route_sequences:
                continue
            seen_route_sequences.add(advisory.route_ids)
            unique.append(advisory)
            if len(unique) >= self.policy.max_advisories:
                break
        return AdvisoryDecision(
            status="published",
            generated_at_ms=now_ms,
            advisories=tuple(unique),
            evaluated_candidate_count=evaluated_count,
            baseline_arrival_time_ms=baseline.arrival_time_ms,
        )

    def _validate_request(self, request: AdvisoryRequest) -> Optional[str]:
        if request.origin_stop_id not in self.topology.stops:
            return "unknown_origin_stop"
        if request.destination_stop_id not in self.topology.stops:
            return "unknown_destination_stop"
        if request.origin_stop_id == request.destination_stop_id:
            return "origin_matches_destination"
        matching_paths = [
            path
            for path in self.topology.trip_paths.values()
            if path.route_id == request.disrupted_route_id
            and (request.direction_id is None or path.direction_id == request.direction_id)
        ]
        if not matching_paths:
            return "unknown_disrupted_route"
        return None

    @staticmethod
    def _resolve_health(
        route_health: RouteHealthLookup,
        route_id: str,
        direction_id: Optional[int],
    ) -> Optional[RouteHealth]:
        if direction_id is not None:
            health = route_health.get((route_id, direction_id))
            if health is None or health.direction_id != direction_id:
                return None
            return health
        health = route_health.get(route_id) or route_health.get((route_id, None))
        if health is None or health.direction_id is not None:
            return None
        return health

    def _disruption_trigger_failure(self, health: Optional[RouteHealth], now_ms: int) -> Optional[str]:
        if health is None:
            return "missing_disruption_health"
        if not self._health_values_are_valid(health):
            return "invalid_disruption_health"
        if not self._health_is_fresh(health, now_ms):
            return "stale_disruption_health"
        if health.confidence < self.policy.disruption_confidence_threshold:
            return "uncorroborated_disruption"
        if health.disruption_score < self.policy.disruption_threshold:
            return "disruption_below_threshold"
        if health.regime and health.regime not in DISRUPTED_REGIMES:
            return "disruption_regime_not_actionable"
        return None

    def _health_is_fresh(self, health: RouteHealth, now_ms: int) -> bool:
        age_ms = now_ms - health.observed_at_ms
        return (
            age_ms >= -self.policy.future_observation_tolerance_seconds * 1000
            and age_ms <= self.policy.max_health_age_seconds * 1000
        )

    @staticmethod
    def _health_values_are_valid(health: RouteHealth) -> bool:
        return 0.0 <= health.disruption_score <= 1.0 and 0.0 <= health.confidence <= 1.0

    def _alternative_health_is_usable(
        self,
        route_id: str,
        direction_id: Optional[int],
        route_health: RouteHealthLookup,
        now_ms: int,
    ) -> bool:
        health = self._resolve_health(route_health, route_id, direction_id)
        return bool(
            health
            and self._health_values_are_valid(health)
            and self._health_is_fresh(health, now_ms)
            and health.confidence >= self.policy.minimum_alternative_health_confidence
            and health.disruption_score <= self.policy.maximum_alternative_disruption_score
            and (not health.regime or health.regime not in DISRUPTED_REGIMES)
        )

    def _prediction_trips(
        self,
        prediction_index: RealtimePredictionIndex,
        now_ms: int,
    ) -> Mapping[str, Tuple[StopPrediction, ...]]:
        output: Dict[str, Tuple[StopPrediction, ...]] = {}
        for trip_id, rows in prediction_index.by_trip.items():
            fresh = tuple(row for row in rows if self._prediction_is_fresh(row, now_ms))
            if fresh:
                output[trip_id] = fresh
        return output

    def _prediction_is_fresh(self, prediction: StopPrediction, now_ms: int) -> bool:
        age_ms = now_ms - prediction.observed_at_ms
        return (
            age_ms >= -self.policy.future_observation_tolerance_seconds * 1000
            and age_ms <= self.policy.max_prediction_age_seconds * 1000
        )

    def _best_segment(
        self,
        prediction_trips: Mapping[str, Tuple[StopPrediction, ...]],
        *,
        route_id: Optional[str],
        from_stop_id: str,
        to_stop_id: str,
        earliest_departure_ms: int,
        direction_id: Optional[int] = None,
        excluded_route_id: Optional[str] = None,
    ) -> Optional[_RideSegment]:
        segments = self._segments(
            prediction_trips,
            route_id=route_id,
            from_stop_id=from_stop_id,
            to_stop_id=to_stop_id,
            earliest_departure_ms=earliest_departure_ms,
            direction_id=direction_id,
            excluded_route_id=excluded_route_id,
        )
        return min(segments, key=lambda segment: (segment.arrival_time_ms, segment.trip_id), default=None)

    def _segments(
        self,
        prediction_trips: Mapping[str, Tuple[StopPrediction, ...]],
        *,
        route_id: Optional[str],
        from_stop_id: str,
        to_stop_id: str,
        earliest_departure_ms: int,
        direction_id: Optional[int] = None,
        excluded_route_id: Optional[str] = None,
    ) -> List[_RideSegment]:
        segments: List[_RideSegment] = []
        candidate_trip_ids = set(self.topology.trips_by_stop.get(from_stop_id, ()))
        candidate_trip_ids.intersection_update(self.topology.trips_by_stop.get(to_stop_id, ()))
        for trip_id in sorted(candidate_trip_ids):
            path = self.topology.trip_paths.get(trip_id)
            if path is None or (route_id is not None and path.route_id != route_id):
                continue
            if direction_id is not None and path.direction_id != direction_id:
                continue
            if excluded_route_id is not None and path.route_id == excluded_route_id:
                continue
            positions_from = [index for index, stop in enumerate(path.stops) if stop.stop_id == from_stop_id]
            positions_to = [index for index, stop in enumerate(path.stops) if stop.stop_id == to_stop_id]
            predictions_by_sequence = {
                row.stop_sequence: row
                for row in prediction_trips.get(trip_id, ())
                if row.direction_id is None
                or path.direction_id is None
                or row.direction_id == path.direction_id
            }
            for from_index in positions_from:
                for to_index in positions_to:
                    if to_index <= from_index:
                        continue
                    from_static = path.stops[from_index]
                    to_static = path.stops[to_index]
                    departure = predictions_by_sequence.get(from_static.stop_sequence)
                    arrival = predictions_by_sequence.get(to_static.stop_sequence)
                    if departure is None or arrival is None:
                        continue
                    if departure.departure_time_ms < earliest_departure_ms:
                        continue
                    if arrival.arrival_time_ms <= departure.departure_time_ms:
                        continue
                    if (
                        departure.departure_time_ms
                        > earliest_departure_ms
                        + self.policy.maximum_initial_wait_seconds * 1000
                    ):
                        continue
                    if arrival.arrival_time_ms > earliest_departure_ms + self.policy.maximum_journey_seconds * 1000:
                        continue
                    expected_sequences = {stop.stop_sequence for stop in path.stops[from_index : to_index + 1]}
                    covered_sequences = expected_sequences.intersection(predictions_by_sequence)
                    coverage = len(covered_sequences) / len(expected_sequences)
                    if coverage < self.policy.minimum_realtime_coverage:
                        continue
                    observed_at_ms = min(
                        predictions_by_sequence[sequence].observed_at_ms for sequence in covered_sequences
                    )
                    segments.append(
                        _RideSegment(
                            trip_id=trip_id,
                            route_id=path.route_id,
                            direction_id=path.direction_id,
                            from_stop_id=from_stop_id,
                            to_stop_id=to_stop_id,
                            departure_time_ms=departure.departure_time_ms,
                            arrival_time_ms=arrival.arrival_time_ms,
                            observed_at_ms=observed_at_ms,
                            realtime_coverage=coverage,
                            departure_time_source=departure.departure_time_source,
                            arrival_time_source=arrival.arrival_time_source,
                            collection_source=departure.collection_source or arrival.collection_source,
                        )
                    )
        return segments

    def _candidate_itineraries(
        self,
        request: AdvisoryRequest,
        prediction_trips: Mapping[str, Tuple[StopPrediction, ...]],
        route_health: RouteHealthLookup,
        now_ms: int,
    ) -> List[_Candidate]:
        access_edges = self._access_edges(request.origin_stop_id)
        egress_edges = self._egress_edges(request.destination_stop_id)
        candidates: Dict[Tuple[Any, ...], _Candidate] = {}

        for access in access_edges:
            board_stop = access.to_stop_id if access else request.origin_stop_id
            access_seconds = access.minimum_transfer_seconds if access else 0
            for egress in egress_edges:
                alight_stop = egress.from_stop_id if egress else request.destination_stop_id
                egress_seconds = egress.minimum_transfer_seconds if egress else 0
                for segment in self._segments(
                    prediction_trips,
                    route_id=None,
                    from_stop_id=board_stop,
                    to_stop_id=alight_stop,
                    earliest_departure_ms=now_ms + access_seconds * 1000,
                    excluded_route_id=request.disrupted_route_id,
                ):
                    if not self._alternative_health_is_usable(
                        segment.route_id,
                        segment.direction_id,
                        route_health,
                        now_ms,
                    ):
                        continue
                    candidate = self._make_candidate(
                        (segment,),
                        access,
                        None,
                        egress,
                        route_health,
                        egress_seconds,
                    )
                    if (
                        candidate is not None
                        and candidate.arrival_time_ms
                        <= now_ms + self.policy.maximum_journey_seconds * 1000
                    ):
                        candidates[self._candidate_key(candidate)] = candidate

        for access in access_edges:
            board_stop = access.to_stop_id if access else request.origin_stop_id
            access_seconds = access.minimum_transfer_seconds if access else 0
            for connection in self._reachable_connection_edges(
                board_stop,
                prediction_trips,
                excluded_route_id=request.disrupted_route_id,
            ):
                first_segments = self._segments(
                    prediction_trips,
                    route_id=None,
                    from_stop_id=board_stop,
                    to_stop_id=connection.from_stop_id,
                    earliest_departure_ms=now_ms + access_seconds * 1000,
                    excluded_route_id=request.disrupted_route_id,
                )
                if not first_segments:
                    continue
                for first in first_segments:
                    if not self._alternative_health_is_usable(
                        first.route_id,
                        first.direction_id,
                        route_health,
                        now_ms,
                    ):
                        continue
                    ready_ms = first.arrival_time_ms + max(
                        connection.minimum_transfer_seconds,
                        self.policy.minimum_transfer_seconds,
                    ) * 1000
                    for egress in egress_edges:
                        final_stop = egress.from_stop_id if egress else request.destination_stop_id
                        egress_seconds = egress.minimum_transfer_seconds if egress else 0
                        for second in self._segments(
                            prediction_trips,
                            route_id=None,
                            from_stop_id=connection.to_stop_id,
                            to_stop_id=final_stop,
                            earliest_departure_ms=ready_ms,
                            excluded_route_id=request.disrupted_route_id,
                        ):
                            if second.route_id == first.route_id:
                                continue
                            if not self._alternative_health_is_usable(
                                second.route_id,
                                second.direction_id,
                                route_health,
                                now_ms,
                            ):
                                continue
                            candidate = self._make_candidate(
                                (first, second),
                                access,
                                connection,
                                egress,
                                route_health,
                                egress_seconds,
                            )
                            if candidate is None:
                                continue
                            if (
                                candidate.arrival_time_ms
                                > now_ms + self.policy.maximum_journey_seconds * 1000
                            ):
                                continue
                            key = self._candidate_key(candidate)
                            current = candidates.get(key)
                            if current is None or candidate.ranking_time_ms < current.ranking_time_ms:
                                candidates[key] = candidate
        return sorted(
            candidates.values(),
            key=lambda candidate: (candidate.ranking_time_ms, self._candidate_key(candidate)),
        )

    def _reachable_connection_edges(
        self,
        board_stop_id: str,
        prediction_trips: Mapping[str, Tuple[StopPrediction, ...]],
        *,
        excluded_route_id: Optional[str] = None,
    ) -> Tuple[TransferEdge, ...]:
        """Limit transfer search to downstream stops on live first-leg trips."""

        edges: Dict[Tuple[str, str, str], TransferEdge] = {}
        seen_patterns: set[Tuple[str, Optional[int], Tuple[TripStop, ...], int]] = set()
        candidate_trip_ids = set(self.topology.trips_by_stop.get(board_stop_id, ()))
        candidate_trip_ids.intersection_update(prediction_trips)
        for trip_id in sorted(candidate_trip_ids):
            path = self.topology.trip_paths.get(trip_id)
            if path is None or path.route_id == excluded_route_id:
                continue
            board_positions = [
                index
                for index, stop in enumerate(path.stops)
                if stop.stop_id == board_stop_id
            ]
            for board_position in board_positions:
                pattern_key = (
                    path.route_id,
                    path.direction_id,
                    path.stops,
                    board_position,
                )
                if pattern_key in seen_patterns:
                    continue
                seen_patterns.add(pattern_key)
                for stop in path.stops[board_position + 1 :]:
                    for edge in self.topology.transfers_from_stop.get(
                        stop.stop_id,
                        (),
                    ):
                        edges[(edge.from_stop_id, edge.to_stop_id, edge.source)] = edge
        return tuple(edges[key] for key in sorted(edges))

    def _access_edges(self, origin_stop_id: str) -> Tuple[Optional[TransferEdge], ...]:
        edges = tuple(
            edge
            for edge in self.topology.transfers_from_stop.get(origin_stop_id, ())
            if edge.to_stop_id != origin_stop_id
        )
        return (None, *edges)

    def _egress_edges(self, destination_stop_id: str) -> Tuple[Optional[TransferEdge], ...]:
        edges = tuple(
            edge
            for edge in self.topology.transfers_to_stop.get(destination_stop_id, ())
            if edge.from_stop_id != destination_stop_id
        )
        return (None, *edges)

    def _make_candidate(
        self,
        segments: Tuple[_RideSegment, ...],
        access_edge: Optional[TransferEdge],
        connection_edge: Optional[TransferEdge],
        egress_edge: Optional[TransferEdge],
        route_health: RouteHealthLookup,
        egress_seconds: int,
    ) -> Optional[_Candidate]:
        walking_edges = tuple(
            edge
            for edge in (access_edge, connection_edge, egress_edge)
            if edge is not None and edge.from_stop_id != edge.to_stop_id
        )
        walk_seconds = sum(
            max(edge.minimum_transfer_seconds, self.policy.minimum_transfer_seconds)
            if edge is connection_edge
            else edge.minimum_transfer_seconds
            for edge in walking_edges
        )
        transfer_seconds = (
            max(
                connection_edge.minimum_transfer_seconds,
                self.policy.minimum_transfer_seconds,
            )
            if connection_edge is not None
            else 0
        )
        known_distances = [edge.distance_meters for edge in walking_edges]
        walk_meters = (
            sum(float(distance) for distance in known_distances if distance is not None)
            if all(distance is not None for distance in known_distances)
            else None
        )
        if walk_seconds > self.policy.maximum_walking_seconds:
            return None
        if walk_meters is not None and walk_meters > self.policy.maximum_walking_meters:
            return None
        arrival_ms = segments[-1].arrival_time_ms + egress_seconds * 1000
        segment_health = [
            self._resolve_health(route_health, segment.route_id, segment.direction_id)
            for segment in segments
        ]
        if any(health is None for health in segment_health):
            return None
        maximum_health_score = max(
            health.disruption_score for health in segment_health if health is not None
        )
        ranking_penalty_seconds = int(round(maximum_health_score * self.policy.health_penalty_seconds))
        if len(segments) > 1:
            ranking_penalty_seconds += self.policy.transfer_penalty_seconds
        return _Candidate(
            ride_segments=segments,
            access_edge=access_edge,
            connection_edge=connection_edge,
            egress_edge=egress_edge,
            arrival_time_ms=arrival_ms,
            ranking_time_ms=arrival_ms + ranking_penalty_seconds * 1000,
            walk_seconds=walk_seconds,
            walk_meters=walk_meters,
            transfer_seconds=transfer_seconds,
        )

    def _build_advisory(
        self,
        request: AdvisoryRequest,
        baseline: _RideSegment,
        candidate: _Candidate,
        route_health: RouteHealthLookup,
        disrupted_health: RouteHealth,
        benefit_seconds: int,
    ) -> AlternativeAdvisory:
        route_ids = tuple(segment.route_id for segment in candidate.ride_segments)
        observed_at_values = [baseline.observed_at_ms]
        observed_at_values.extend(segment.observed_at_ms for segment in candidate.ride_segments)
        oldest_observation_ms = min(observed_at_values)
        coverage = min(
            [baseline.realtime_coverage]
            + [segment.realtime_coverage for segment in candidate.ride_segments]
        )
        freshness = 1.0 - (
            (request.requested_at_ms - oldest_observation_ms)
            / max(1, self.policy.max_prediction_age_seconds * 1000)
        )
        freshness = max(0.0, min(1.0, freshness))
        health_confidence = min(
            [disrupted_health.confidence]
            + [
                health.confidence
                for segment in candidate.ride_segments
                for health in [
                    self._resolve_health(route_health, segment.route_id, segment.direction_id)
                ]
                if health is not None
            ]
        )
        benefit_strength = min(
            1.0,
            benefit_seconds / max(1, self.policy.minimum_benefit_seconds * 2),
        )
        confidence = round(
            max(
                0.0,
                min(
                    1.0,
                    (0.40 * health_confidence)
                    + (0.30 * coverage)
                    + (0.20 * freshness)
                    + (0.10 * benefit_strength),
                ),
            ),
            3,
        )
        confidence_label = "high" if confidence >= 0.82 else "medium" if confidence >= 0.62 else "low"

        first_departure_ms = candidate.ride_segments[0].departure_time_ms
        expires_at_ms = min(
            first_departure_ms,
            request.requested_at_ms + self.policy.advisory_ttl_seconds * 1000,
            oldest_observation_ms + self.policy.max_prediction_age_seconds * 1000,
        )
        legs = self._advisory_legs(request, candidate)
        route_text = " → ".join(self._route_label(route_id) for route_id in route_ids)
        disrupted_text = self._route_label(request.disrupted_route_id)
        origin_text = self.topology.stops[request.origin_stop_id].stop_name or request.origin_stop_id
        destination_text = (
            self.topology.stops[request.destination_stop_id].stop_name or request.destination_stop_id
        )
        minutes_saved = max(1, int(round(benefit_seconds / 60)))
        summary = (
            f"For riders at {origin_text} heading toward {destination_text}, {route_text} is estimated "
            f"to arrive {minutes_saved} minutes earlier than staying on {disrupted_text}."
        )
        explanation = (
            f"Confidence: {confidence_label}; based on fresh live predicted arrivals, "
            "static stop order, explicit walking/transfer time, and current route health."
        )
        evidence: List[AdvisoryEvidence] = [
            AdvisoryEvidence(
                "disruption_trigger",
                {
                    "route_id": request.disrupted_route_id,
                    "direction_id": disrupted_health.direction_id,
                    "regime": disrupted_health.regime,
                    "disruption_score": disrupted_health.disruption_score,
                    "confidence": disrupted_health.confidence,
                    "observed_at_ms": disrupted_health.observed_at_ms,
                },
            ),
            AdvisoryEvidence(
                "baseline_prediction",
                {
                    "route_id": baseline.route_id,
                    "direction_id": baseline.direction_id,
                    "trip_id": baseline.trip_id,
                    "arrival_time_ms": baseline.arrival_time_ms,
                    "realtime_coverage": baseline.realtime_coverage,
                    "observed_at_ms": baseline.observed_at_ms,
                },
            ),
        ]
        for segment in candidate.ride_segments:
            health = self._resolve_health(route_health, segment.route_id, segment.direction_id)
            if health is None:
                continue
            evidence.append(
                AdvisoryEvidence(
                    "alternative_prediction",
                    {
                        "route_id": segment.route_id,
                        "direction_id": segment.direction_id,
                        "trip_id": segment.trip_id,
                        "from_stop_id": segment.from_stop_id,
                        "to_stop_id": segment.to_stop_id,
                        "departure_time_ms": segment.departure_time_ms,
                        "arrival_time_ms": segment.arrival_time_ms,
                        "realtime_coverage": segment.realtime_coverage,
                        "observed_at_ms": segment.observed_at_ms,
                        "departure_time_source": segment.departure_time_source,
                        "arrival_time_source": segment.arrival_time_source,
                        "collection_source": segment.collection_source,
                        "route_disruption_score": health.disruption_score,
                        "route_health_confidence": health.confidence,
                    },
                )
            )
        if candidate.connection_edge is not None:
            evidence.append(
                AdvisoryEvidence(
                    "transfer",
                    {
                        "from_stop_id": candidate.connection_edge.from_stop_id,
                        "to_stop_id": candidate.connection_edge.to_stop_id,
                        "minimum_transfer_seconds": max(
                            candidate.connection_edge.minimum_transfer_seconds,
                            self.policy.minimum_transfer_seconds,
                        ),
                        "source": candidate.connection_edge.source,
                        "provenance": (
                            "agency_defined"
                            if candidate.connection_edge.source == "agency_defined"
                            else "inferred"
                            if candidate.connection_edge.source.startswith("inferred_")
                            else "configured"
                        ),
                    },
                )
            )
        evidence.append(
            AdvisoryEvidence(
                "walking",
                {
                    "total_walking_seconds": candidate.walk_seconds,
                    "total_walking_meters": candidate.walk_meters,
                    "maximum_walking_seconds": self.policy.maximum_walking_seconds,
                    "maximum_walking_meters": self.policy.maximum_walking_meters,
                },
            )
        )
        evidence.append(
            AdvisoryEvidence(
                "transfer_time",
                {"total_transfer_seconds": candidate.transfer_seconds},
            )
        )
        evidence.append(
            AdvisoryEvidence(
                "ranking",
                {
                    "predicted_arrival_time_ms": candidate.arrival_time_ms,
                    "health_adjusted_arrival_time_ms": candidate.ranking_time_ms,
                    "expected_time_saved_seconds": benefit_seconds,
                },
            )
        )
        return AlternativeAdvisory(
            disrupted_route_id=request.disrupted_route_id,
            origin_stop_id=request.origin_stop_id,
            destination_stop_id=request.destination_stop_id,
            route_ids=route_ids,
            estimated_arrival_time_ms=candidate.arrival_time_ms,
            baseline_arrival_time_ms=baseline.arrival_time_ms,
            expected_time_saved_seconds=benefit_seconds,
            total_walking_seconds=candidate.walk_seconds,
            total_walking_meters=candidate.walk_meters,
            total_transfer_seconds=candidate.transfer_seconds,
            confidence=confidence,
            confidence_label=confidence_label,
            expires_at_ms=expires_at_ms,
            summary=summary,
            explanation=explanation,
            legs=legs,
            evidence=tuple(evidence),
        )

    def _route_label(self, route_id: str) -> str:
        return self.topology.route_labels.get(route_id, route_id)

    def _advisory_legs(
        self,
        request: AdvisoryRequest,
        candidate: _Candidate,
    ) -> Tuple[AdvisoryLeg, ...]:
        legs: List[AdvisoryLeg] = []
        cursor_ms = request.requested_at_ms
        if candidate.access_edge is not None:
            edge = candidate.access_edge
            arrival_ms = cursor_ms + edge.minimum_transfer_seconds * 1000
            legs.append(self._walk_leg(edge, cursor_ms, arrival_ms))
            cursor_ms = arrival_ms
        for index, segment in enumerate(candidate.ride_segments):
            legs.append(
                AdvisoryLeg(
                    kind="ride",
                    from_stop_id=segment.from_stop_id,
                    to_stop_id=segment.to_stop_id,
                    departure_time_ms=segment.departure_time_ms,
                    arrival_time_ms=segment.arrival_time_ms,
                    duration_seconds=int((segment.arrival_time_ms - segment.departure_time_ms) / 1000),
                    route_id=segment.route_id,
                    trip_id=segment.trip_id,
                    direction_id=segment.direction_id,
                    realtime_coverage=segment.realtime_coverage,
                )
            )
            cursor_ms = segment.arrival_time_ms
            if index == 0 and candidate.connection_edge is not None:
                edge = candidate.connection_edge
                duration = max(edge.minimum_transfer_seconds, self.policy.minimum_transfer_seconds)
                arrival_ms = cursor_ms + duration * 1000
                legs.append(
                    self._walk_leg(
                        edge,
                        cursor_ms,
                        arrival_ms,
                        duration_seconds=duration,
                        kind="transfer"
                        if edge.from_stop_id == edge.to_stop_id
                        else "walk",
                    )
                )
                cursor_ms = arrival_ms
        if candidate.egress_edge is not None:
            edge = candidate.egress_edge
            arrival_ms = cursor_ms + edge.minimum_transfer_seconds * 1000
            legs.append(self._walk_leg(edge, cursor_ms, arrival_ms))
        return tuple(legs)

    @staticmethod
    def _walk_leg(
        edge: TransferEdge,
        departure_ms: int,
        arrival_ms: int,
        *,
        duration_seconds: Optional[int] = None,
        kind: str = "walk",
    ) -> AdvisoryLeg:
        return AdvisoryLeg(
            kind=kind,
            from_stop_id=edge.from_stop_id,
            to_stop_id=edge.to_stop_id,
            departure_time_ms=departure_ms,
            arrival_time_ms=arrival_ms,
            duration_seconds=edge.minimum_transfer_seconds if duration_seconds is None else duration_seconds,
            transfer_source=edge.source,
        )

    @staticmethod
    def _candidate_key(candidate: _Candidate) -> Tuple[Any, ...]:
        return tuple(
            (segment.route_id, segment.trip_id, segment.from_stop_id, segment.to_stop_id)
            for segment in candidate.ride_segments
        )

    @staticmethod
    def _advisory_key(advisory: AlternativeAdvisory) -> Tuple[Any, ...]:
        return tuple(
            (leg.route_id, leg.trip_id, leg.from_stop_id, leg.to_stop_id)
            for leg in advisory.legs
            if leg.kind == "ride"
        )

    @staticmethod
    def _suppressed(now_ms: int, reason: str) -> AdvisoryDecision:
        return AdvisoryDecision(
            status="suppressed",
            generated_at_ms=now_ms,
            suppression_reasons=(reason,),
        )


def _trip_relationship_is_routable(relationship: str) -> bool:
    """Only accept relationships whose service semantics are unambiguous here."""

    if relationship in NON_RUNNING_TRIP_SCHEDULE_RELATIONSHIPS:
        return False
    if relationship in UNSUPPORTED_TRIP_SCHEDULE_RELATIONSHIPS:
        return False
    return relationship in ROUTABLE_TRIP_SCHEDULE_RELATIONSHIPS


def _set_trip_relationship(
    records: Dict[str, Tuple[int, str]],
    trip_id: str,
    observed_at_ms: int,
    relationship: Any,
) -> None:
    if not trip_id:
        return
    normalized = normalize_trip_schedule_relationship(relationship) or "SCHEDULED"
    candidate = (int(observed_at_ms), normalized)
    current = records.get(trip_id)
    if current is None or candidate[0] > current[0]:
        records[trip_id] = candidate
        return
    if candidate[0] != current[0]:
        return
    # At an equal timestamp, fail closed on conflicting descriptors.  The
    # lexical tiebreak keeps two equally usable or unusable values deterministic.
    candidate_priority = int(not _trip_relationship_is_routable(candidate[1]))
    current_priority = int(not _trip_relationship_is_routable(current[1]))
    if (candidate_priority, candidate[1]) >= (current_priority, current[1]):
        records[trip_id] = candidate


def _blocked_trip_ids(
    records: Mapping[str, Tuple[int, str]],
) -> frozenset[str]:
    return frozenset(
        trip_id
        for trip_id, (_, relationship) in records.items()
        if not _trip_relationship_is_routable(relationship)
    )


def _epoch_milliseconds(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    return value if value >= 1_000_000_000_000 else value * 1000


def _optional_int_value(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _find_trip_path_stop(
    path: Optional[TripPath],
    *,
    stop_sequence: Optional[int],
    stop_id: Optional[str],
) -> Optional[TripStop]:
    if path is None:
        return None
    for stop in path.stops:
        if stop_sequence is not None and stop.stop_sequence == stop_sequence:
            if stop_id is None or stop.stop_id == stop_id:
                return stop
        elif stop_sequence is None and stop_id and stop.stop_id == stop_id:
            return stop
    return None


def _set_prediction_record(
    records: Dict[Tuple[str, str, int], Tuple[int, str, Optional[StopPrediction]]],
    key: Tuple[str, str, int],
    observed_at_ms: int,
    status: str,
    prediction: Optional[StopPrediction],
) -> None:
    current = records.get(key)
    priority = {"PREDICTION": 0, "UNAVAILABLE": 1, "NO_DATA": 2, "SKIPPED": 3}
    if current is None or observed_at_ms > current[0]:
        records[key] = (observed_at_ms, status, prediction)
    elif observed_at_ms == current[0] and priority[status] >= priority[current[1]]:
        records[key] = (observed_at_ms, status, prediction)


def _defensible_transfer_seconds(
    *,
    from_stop_id: str,
    to_stop_id: str,
    distance_meters: Optional[float],
    walking_speed_mps: float,
    station_transfer_floor_seconds: int,
    shared_parent_station: bool,
) -> int:
    """Avoid zero-duration movement between distinct stops without an explicit minimum."""

    if from_stop_id == to_stop_id:
        return 0
    walk_seconds = 0
    if distance_meters is not None:
        walk_seconds = int(math.ceil(distance_meters / walking_speed_mps))
    floor_seconds = station_transfer_floor_seconds if distance_meters is None or shared_parent_station else 0
    return max(1, floor_seconds, walk_seconds)


def _share_parent_station(first: Optional[GTFSStop], second: Optional[GTFSStop]) -> bool:
    return bool(
        first is not None
        and second is not None
        and first.parent_station
        and first.parent_station == second.parent_station
    )


def _stop_distance_meters(first: Optional[GTFSStop], second: Optional[GTFSStop]) -> Optional[float]:
    if (
        first is None
        or second is None
        or first.stop_lat is None
        or first.stop_lon is None
        or second.stop_lat is None
        or second.stop_lon is None
    ):
        return None
    lat1 = math.radians(first.stop_lat)
    lat2 = math.radians(second.stop_lat)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(second.stop_lon - first.stop_lon)
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 6_371_000.0 * 2 * math.asin(math.sqrt(haversine))


__all__ = [
    "AdvisoryDecision",
    "AdvisoryEvidence",
    "AdvisoryLeg",
    "AdvisoryPolicy",
    "AdvisoryRequest",
    "AlternativeAdvisory",
    "AlternativeServiceAdvisor",
    "ExplicitTransfer",
    "RealtimePredictionIndex",
    "RideEdge",
    "RouteHealth",
    "StopPrediction",
    "TransferEdge",
    "TransitTopology",
    "TripPath",
    "TripStop",
    "build_transit_topology",
    "compile_transit_topology",
    "load_transit_topology",
    "save_transit_topology",
]
