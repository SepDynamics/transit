#!/usr/bin/env python3
"""Compile a deterministic, runtime-loadable topology from a static GTFS feed.

Unlike :func:`scripts.transit.feeds.load_gtfs_catalog`, this compiler streams
``stop_times.txt``.  It retains one trip group at a time, compact shared route
patterns, and aggregate adjacent-stop statistics rather than a second in-memory
copy of every stop-time row.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, TextIO, Tuple
from zipfile import BadZipFile, ZipFile, is_zipfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.transit.advisory import (  # noqa: E402
    RideEdge,
    TOPOLOGY_ARTIFACT_VERSION,
    TransferEdge,
    TransitTopology,
    TripPath,
    TripStop,
    build_transit_topology,
    save_transit_topology,
)
from scripts.transit.transit_types import (  # noqa: E402
    GTFSRoute,
    GTFSStop,
    GTFSTransfer,
    GTFSTrip,
    parse_gtfs_time_to_seconds,
)


EARTH_RADIUS_METERS = 6_371_000.0


class TopologyCompileError(ValueError):
    """A clear, source-localized GTFS compilation failure."""


@dataclass(frozen=True)
class CompiledTopology:
    topology: TransitTopology
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class _StopTimeRow:
    stop_id: str
    stop_sequence: int
    arrival_seconds: Optional[int]
    departure_seconds: Optional[int]


@dataclass
class _RideEdgeStats:
    trip_id: str
    from_stop_sequence: int
    to_stop_sequence: int
    duration_total: int = 0
    duration_count: int = 0


PatternSignature = Tuple[str, Optional[int], Tuple[Tuple[str, int], ...]]
RideEdgeKey = Tuple[str, Optional[int], str, str]


class _GTFSFeed:
    """Open individual GTFS tables without loading a zip archive into memory."""

    def __init__(self, source: str | Path) -> None:
        self.source = Path(source)
        self.kind = ""
        self._archive: Optional[ZipFile] = None
        self._members: Dict[str, str] = {}

    def __enter__(self) -> "_GTFSFeed":
        if self.source.is_dir():
            self.kind = "directory"
            return self
        if not self.source.is_file():
            raise TopologyCompileError(f"GTFS source does not exist: {self.source}")
        if not is_zipfile(self.source):
            raise TopologyCompileError(
                f"GTFS source must be a directory or zip archive: {self.source}"
            )
        try:
            self._archive = ZipFile(self.source, "r")
        except BadZipFile as exc:
            raise TopologyCompileError(f"invalid GTFS zip archive: {self.source}") from exc
        self.kind = "zip"
        for member in self._archive.namelist():
            basename = Path(member).name
            if not basename or basename not in {
                "routes.txt",
                "trips.txt",
                "stops.txt",
                "stop_times.txt",
                "transfers.txt",
            }:
                continue
            if basename in self._members:
                self._archive.close()
                self._archive = None
                raise TopologyCompileError(
                    f"GTFS zip contains more than one {basename}; table selection is ambiguous"
                )
            self._members[basename] = member
        return self

    def __exit__(self, *_args: object) -> None:
        if self._archive is not None:
            self._archive.close()

    @contextmanager
    def open_table(self, name: str, *, required: bool) -> Iterator[Optional[TextIO]]:
        if self.kind == "directory":
            table_path = self.source / name
            if not table_path.exists():
                if required:
                    raise TopologyCompileError(f"required GTFS table is missing: {name}")
                yield None
                return
            with table_path.open("r", encoding="utf-8-sig", newline="") as handle:
                yield handle
            return

        member = self._members.get(name)
        if member is None:
            if required:
                raise TopologyCompileError(f"required GTFS table is missing: {name}")
            yield None
            return
        assert self._archive is not None
        with self._archive.open(member, "r") as raw_handle:
            with io.TextIOWrapper(raw_handle, encoding="utf-8-sig", newline="") as handle:
                yield handle


def compile_gtfs_topology(
    source: str | Path,
    *,
    feed_label: Optional[str] = None,
    max_nearby_walk_meters: float = 250.0,
    walking_speed_mps: float = 1.2,
    station_transfer_floor_seconds: int = 60,
) -> CompiledTopology:
    """Stream a zip/directory GTFS feed into the advisory topology API."""

    _validate_options(
        max_nearby_walk_meters=max_nearby_walk_meters,
        walking_speed_mps=walking_speed_mps,
        station_transfer_floor_seconds=station_transfer_floor_seconds,
    )
    source_path = Path(source)
    resolved_feed_label = feed_label or _default_feed_label(source_path)

    with _GTFSFeed(source_path) as feed:
        routes = _load_routes(feed)
        stops = _load_stops(feed)
        trips = _load_trips(feed, routes)
        transfers = _load_transfers(feed)
        (
            trip_signatures,
            pattern_signatures,
            ride_edge_stats,
            routes_by_stop,
            stop_time_count,
        ) = _stream_stop_times(feed, trips, stops)

    ride_edges = _build_ride_edges(ride_edge_stats)
    transfer_edges, transfer_counts = _build_transfer_edges(
        stops=stops,
        routes_by_stop=routes_by_stop,
        transfers=transfers,
        max_nearby_walk_meters=max_nearby_walk_meters,
        walking_speed_mps=walking_speed_mps,
        station_transfer_floor_seconds=station_transfer_floor_seconds,
    )
    topology = _build_topology(
        feed_label=resolved_feed_label,
        routes=routes,
        stops=stops,
        pattern_signatures=pattern_signatures,
        trip_signatures=trip_signatures,
        ride_edges=ride_edges,
        transfer_edges=transfer_edges,
    )
    counts = {
        "routes": len(routes),
        "stops": len(stops),
        "served_stops": len(routes_by_stop),
        "source_trips": len(trips),
        "compiled_trips": len(trip_signatures),
        "trips_without_stop_times": len(trips) - len(trip_signatures),
        "stop_time_rows": stop_time_count,
        "patterns": len(pattern_signatures),
        "ride_edges": len(ride_edges),
        "source_transfers": len(transfers),
        "transfer_edges": len(transfer_edges),
        **transfer_counts,
    }
    metadata: Dict[str, Any] = {
        "artifact_schema_version": TOPOLOGY_ARTIFACT_VERSION,
        "compiler": "scripts.transit.compile_topology",
        "counts": counts,
        "parameters": {
            "max_nearby_walk_meters": max_nearby_walk_meters,
            "station_transfer_floor_seconds": station_transfer_floor_seconds,
            "walking_speed_mps": walking_speed_mps,
        },
    }
    return CompiledTopology(topology=topology, metadata=metadata)


def compile_and_save_gtfs_topology(
    source: str | Path,
    destination: str | Path,
    *,
    feed_label: Optional[str] = None,
    max_nearby_walk_meters: float = 250.0,
    walking_speed_mps: float = 1.2,
    station_transfer_floor_seconds: int = 60,
) -> CompiledTopology:
    """Compile and atomically replace a deterministic JSON or JSON-gzip artifact."""

    source_path = Path(source)
    destination_path = Path(destination)
    if source_path.resolve() == destination_path.resolve():
        raise TopologyCompileError("GTFS source and topology destination must be different paths")
    compiled = compile_gtfs_topology(
        source_path,
        feed_label=feed_label,
        max_nearby_walk_meters=max_nearby_walk_meters,
        walking_speed_mps=walking_speed_mps,
        station_transfer_floor_seconds=station_transfer_floor_seconds,
    )
    _atomic_save(compiled, destination_path)
    return compiled


def _load_routes(feed: _GTFSFeed) -> Dict[str, GTFSRoute]:
    routes: Dict[str, GTFSRoute] = {}
    for row, line_number in _table_rows(feed, "routes.txt", {"route_id"}, required=True):
        route_id = _required_text(row, "route_id", "routes.txt", line_number)
        if route_id in routes:
            raise _row_error("routes.txt", line_number, f"duplicate route_id {route_id!r}")
        routes[route_id] = GTFSRoute(
            route_id=route_id,
            route_short_name=_text(row.get("route_short_name")),
            route_long_name=_text(row.get("route_long_name")),
            route_desc=_text(row.get("route_desc")),
            route_type=_optional_int(row.get("route_type"), "routes.txt", line_number, "route_type"),
            agency_id=_optional_text(row.get("agency_id")),
        )
    return routes


def _load_stops(feed: _GTFSFeed) -> Dict[str, GTFSStop]:
    stops: Dict[str, GTFSStop] = {}
    for row, line_number in _table_rows(feed, "stops.txt", {"stop_id"}, required=True):
        stop_id = _required_text(row, "stop_id", "stops.txt", line_number)
        if stop_id in stops:
            raise _row_error("stops.txt", line_number, f"duplicate stop_id {stop_id!r}")
        latitude = _optional_float(row.get("stop_lat"), "stops.txt", line_number, "stop_lat")
        longitude = _optional_float(row.get("stop_lon"), "stops.txt", line_number, "stop_lon")
        if latitude is not None and not -90.0 <= latitude <= 90.0:
            raise _row_error("stops.txt", line_number, "stop_lat must be between -90 and 90")
        if longitude is not None and not -180.0 <= longitude <= 180.0:
            raise _row_error("stops.txt", line_number, "stop_lon must be between -180 and 180")
        stops[stop_id] = GTFSStop(
            stop_id=stop_id,
            stop_name=_text(row.get("stop_name") or row.get("stop_code")),
            stop_lat=latitude,
            stop_lon=longitude,
            parent_station=_optional_text(row.get("parent_station")),
            location_type=_optional_int(
                row.get("location_type"), "stops.txt", line_number, "location_type"
            ),
        )
    return stops


def _load_trips(feed: _GTFSFeed, routes: Mapping[str, GTFSRoute]) -> Dict[str, GTFSTrip]:
    trips: Dict[str, GTFSTrip] = {}
    required_columns = {"trip_id", "route_id"}
    for row, line_number in _table_rows(feed, "trips.txt", required_columns, required=True):
        trip_id = _required_text(row, "trip_id", "trips.txt", line_number)
        route_id = _required_text(row, "route_id", "trips.txt", line_number)
        if trip_id in trips:
            raise _row_error("trips.txt", line_number, f"duplicate trip_id {trip_id!r}")
        if route_id not in routes:
            raise _row_error(
                "trips.txt", line_number, f"trip {trip_id!r} references unknown route_id {route_id!r}"
            )
        trips[trip_id] = GTFSTrip(
            trip_id=trip_id,
            route_id=route_id,
            service_id=_text(row.get("service_id")),
            trip_headsign=_text(row.get("trip_headsign")),
            direction_id=_optional_int(
                row.get("direction_id"), "trips.txt", line_number, "direction_id"
            ),
            shape_id=_optional_text(row.get("shape_id")),
            block_id=_optional_text(row.get("block_id")),
        )
    return trips


def _load_transfers(feed: _GTFSFeed) -> Tuple[GTFSTransfer, ...]:
    transfers = []
    required_columns = {"from_stop_id", "to_stop_id"}
    for row, line_number in _table_rows(feed, "transfers.txt", required_columns, required=False):
        from_stop_id = _required_text(row, "from_stop_id", "transfers.txt", line_number)
        to_stop_id = _required_text(row, "to_stop_id", "transfers.txt", line_number)
        transfer_type = _optional_int(
            row.get("transfer_type"), "transfers.txt", line_number, "transfer_type"
        )
        minimum = _optional_int(
            row.get("min_transfer_time"), "transfers.txt", line_number, "min_transfer_time"
        )
        if transfer_type is not None and transfer_type < 0:
            raise _row_error("transfers.txt", line_number, "transfer_type must be non-negative")
        if minimum is not None and minimum < 0:
            raise _row_error("transfers.txt", line_number, "min_transfer_time must be non-negative")
        transfers.append(
            GTFSTransfer(
                from_stop_id=from_stop_id,
                to_stop_id=to_stop_id,
                transfer_type=0 if transfer_type is None else transfer_type,
                min_transfer_time=minimum,
            )
        )
    return tuple(transfers)


def _stream_stop_times(
    feed: _GTFSFeed,
    trips: Mapping[str, GTFSTrip],
    stops: Mapping[str, GTFSStop],
) -> Tuple[
    Dict[str, PatternSignature],
    set[PatternSignature],
    Dict[RideEdgeKey, _RideEdgeStats],
    Dict[str, set[str]],
    int,
]:
    trip_signatures: Dict[str, PatternSignature] = {}
    interned_patterns: Dict[PatternSignature, PatternSignature] = {}
    ride_edge_stats: Dict[RideEdgeKey, _RideEdgeStats] = {}
    routes_by_stop: Dict[str, set[str]] = {}
    current_trip_id: Optional[str] = None
    current_rows: list[_StopTimeRow] = []
    stop_time_count = 0

    def finish_trip() -> None:
        nonlocal current_rows, current_trip_id
        if current_trip_id is None:
            return
        trip = trips[current_trip_id]
        raw_signature: PatternSignature = (
            trip.route_id,
            trip.direction_id,
            tuple((row.stop_id, row.stop_sequence) for row in current_rows),
        )
        signature = interned_patterns.setdefault(raw_signature, raw_signature)
        trip_signatures[current_trip_id] = signature
        for row in current_rows:
            routes_by_stop.setdefault(row.stop_id, set()).add(trip.route_id)
        for first, second in zip(current_rows, current_rows[1:]):
            edge_key: RideEdgeKey = (
                trip.route_id,
                trip.direction_id,
                first.stop_id,
                second.stop_id,
            )
            stats = ride_edge_stats.get(edge_key)
            if stats is None:
                stats = _RideEdgeStats(
                    trip_id=current_trip_id,
                    from_stop_sequence=first.stop_sequence,
                    to_stop_sequence=second.stop_sequence,
                )
                ride_edge_stats[edge_key] = stats
            elif current_trip_id < stats.trip_id:
                stats.trip_id = current_trip_id
                stats.from_stop_sequence = first.stop_sequence
                stats.to_stop_sequence = second.stop_sequence
            departure = first.departure_seconds
            if departure is None:
                departure = first.arrival_seconds
            arrival = second.arrival_seconds
            if arrival is None:
                arrival = second.departure_seconds
            if departure is not None and arrival is not None and arrival >= departure:
                stats.duration_total += arrival - departure
                stats.duration_count += 1
        current_rows = []

    required_columns = {"trip_id", "stop_id", "stop_sequence"}
    for row, line_number in _table_rows(feed, "stop_times.txt", required_columns, required=True):
        trip_id = _required_text(row, "trip_id", "stop_times.txt", line_number)
        stop_id = _required_text(row, "stop_id", "stop_times.txt", line_number)
        sequence = _required_int(row, "stop_sequence", "stop_times.txt", line_number)
        if sequence < 0:
            raise _row_error("stop_times.txt", line_number, "stop_sequence must be non-negative")
        if trip_id not in trips:
            raise _row_error(
                "stop_times.txt", line_number, f"references unknown trip_id {trip_id!r}"
            )
        if stop_id not in stops:
            raise _row_error(
                "stop_times.txt", line_number, f"references unknown stop_id {stop_id!r}"
            )
        if trip_id != current_trip_id:
            finish_trip()
            if trip_id in trip_signatures:
                raise _row_error(
                    "stop_times.txt",
                    line_number,
                    f"trip_id {trip_id!r} appears in multiple groups; stop_times.txt must be grouped by trip_id",
                )
            current_trip_id = trip_id
        if current_rows and sequence <= current_rows[-1].stop_sequence:
            raise _row_error(
                "stop_times.txt",
                line_number,
                f"trip_id {trip_id!r} has non-increasing stop_sequence {sequence}; rows must be ordered",
            )
        current_rows.append(
            _StopTimeRow(
                stop_id=stop_id,
                stop_sequence=sequence,
                arrival_seconds=_optional_gtfs_time(
                    row.get("arrival_time"), "stop_times.txt", line_number, "arrival_time"
                ),
                departure_seconds=_optional_gtfs_time(
                    row.get("departure_time"), "stop_times.txt", line_number, "departure_time"
                ),
            )
        )
        stop_time_count += 1
    finish_trip()
    return (
        trip_signatures,
        set(interned_patterns),
        ride_edge_stats,
        routes_by_stop,
        stop_time_count,
    )


def _build_ride_edges(stats_by_edge: Mapping[RideEdgeKey, _RideEdgeStats]) -> Tuple[RideEdge, ...]:
    edges = []
    for (route_id, direction_id, from_stop_id, to_stop_id), stats in sorted(
        stats_by_edge.items(), key=lambda item: _ride_edge_sort_key(item[0])
    ):
        duration = None
        if stats.duration_count:
            duration = int(round(stats.duration_total / stats.duration_count))
        edges.append(
            RideEdge(
                from_stop_id=from_stop_id,
                to_stop_id=to_stop_id,
                route_id=route_id,
                direction_id=direction_id,
                trip_id=stats.trip_id,
                from_stop_sequence=stats.from_stop_sequence,
                to_stop_sequence=stats.to_stop_sequence,
                scheduled_travel_seconds=duration,
            )
        )
    return tuple(edges)


def _build_transfer_edges(
    *,
    stops: Mapping[str, GTFSStop],
    routes_by_stop: Mapping[str, set[str]],
    transfers: Iterable[GTFSTransfer],
    max_nearby_walk_meters: float,
    walking_speed_mps: float,
    station_transfer_floor_seconds: int,
) -> Tuple[Tuple[TransferEdge, ...], Dict[str, int]]:
    transfer_by_pair: Dict[Tuple[str, str], TransferEdge] = {}
    blocked_transfer_pairs: set[Tuple[str, str]] = set()
    source_priority = {
        "inferred_nearby_stop": 0,
        "inferred_shared_station": 1,
        "inferred_shared_stop": 2,
        "agency_defined": 3,
    }

    def block_transfer(from_stop_id: str, to_stop_id: str) -> None:
        pair = (from_stop_id, to_stop_id)
        blocked_transfer_pairs.add(pair)
        transfer_by_pair.pop(pair, None)

    def add_transfer(edge: TransferEdge) -> None:
        pair = (edge.from_stop_id, edge.to_stop_id)
        if pair in blocked_transfer_pairs:
            return
        current = transfer_by_pair.get(pair)
        if current is None or source_priority[edge.source] > source_priority[current.source]:
            transfer_by_pair[pair] = edge
        elif (
            source_priority[edge.source] == source_priority[current.source]
            and edge.minimum_transfer_seconds < current.minimum_transfer_seconds
        ):
            transfer_by_pair[pair] = edge

    served_stop_ids = sorted(routes_by_stop)
    for stop_id in served_stop_ids:
        if len(routes_by_stop[stop_id]) >= 2:
            add_transfer(TransferEdge(stop_id, stop_id, 0, 0.0, "inferred_shared_stop"))

    platforms_by_parent: Dict[str, list[str]] = {}
    for stop_id in served_stop_ids:
        parent = stops[stop_id].parent_station
        if parent:
            platforms_by_parent.setdefault(parent, []).append(stop_id)
    for platform_ids in platforms_by_parent.values():
        for from_stop_id in sorted(platform_ids):
            for to_stop_id in sorted(platform_ids):
                if from_stop_id == to_stop_id:
                    continue
                distance = _stop_distance_meters(stops[from_stop_id], stops[to_stop_id])
                walk_seconds = station_transfer_floor_seconds
                if distance is not None:
                    walk_seconds = max(walk_seconds, int(math.ceil(distance / walking_speed_mps)))
                add_transfer(
                    TransferEdge(
                        from_stop_id=from_stop_id,
                        to_stop_id=to_stop_id,
                        minimum_transfer_seconds=walk_seconds,
                        distance_meters=distance,
                        source="inferred_shared_station",
                    )
                )

    if max_nearby_walk_meters > 0:
        for from_stop_id, to_stop_id, distance in _nearby_stop_pairs(
            stops, served_stop_ids, max_nearby_walk_meters
        ):
            walk_seconds = int(math.ceil(distance / walking_speed_mps))
            add_transfer(
                TransferEdge(
                    from_stop_id,
                    to_stop_id,
                    walk_seconds,
                    distance,
                    "inferred_nearby_stop",
                )
            )
            add_transfer(
                TransferEdge(
                    to_stop_id,
                    from_stop_id,
                    walk_seconds,
                    distance,
                    "inferred_nearby_stop",
                )
            )

    prohibited_count = 0
    agency_count = 0
    for transfer in transfers:
        if transfer.transfer_type == 3:
            prohibited_count += 1
            block_transfer(transfer.from_stop_id, transfer.to_stop_id)
            continue
        if transfer.from_stop_id not in stops or transfer.to_stop_id not in stops:
            raise TopologyCompileError(
                "transfers.txt references an unknown stop: "
                f"{transfer.from_stop_id!r} -> {transfer.to_stop_id!r}"
            )
        from_stop = stops[transfer.from_stop_id]
        to_stop = stops[transfer.to_stop_id]
        distance = _stop_distance_meters(from_stop, to_stop)
        supplied_minimum_seconds = 0
        if transfer.transfer_type == 2 and transfer.min_transfer_time is not None:
            supplied_minimum_seconds = transfer.min_transfer_time
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
                from_stop_id=transfer.from_stop_id,
                to_stop_id=transfer.to_stop_id,
                minimum_transfer_seconds=transfer_seconds,
                distance_meters=distance,
                source="agency_defined",
            )
        )
        agency_count += 1

    edges = tuple(
        sorted(
            transfer_by_pair.values(),
            key=lambda edge: (edge.from_stop_id, edge.to_stop_id, edge.source),
        )
    )
    return edges, {
        "agency_transfer_records": agency_count,
        "prohibited_transfer_records": prohibited_count,
        "inferred_shared_stop_edges": sum(edge.source == "inferred_shared_stop" for edge in edges),
        "inferred_shared_station_edges": sum(
            edge.source == "inferred_shared_station" for edge in edges
        ),
        "inferred_nearby_stop_edges": sum(edge.source == "inferred_nearby_stop" for edge in edges),
    }


def _build_topology(
    *,
    feed_label: str,
    routes: Mapping[str, GTFSRoute],
    stops: Mapping[str, GTFSStop],
    pattern_signatures: Iterable[PatternSignature],
    trip_signatures: Mapping[str, PatternSignature],
    ride_edges: Iterable[RideEdge],
    transfer_edges: Iterable[TransferEdge],
) -> TransitTopology:
    signatures = sorted(pattern_signatures, key=_pattern_sort_key)
    stops_by_pattern = {
        signature: tuple(TripStop(stop_id=stop_id, stop_sequence=sequence) for stop_id, sequence in signature[2])
        for signature in signatures
    }
    trip_paths = {
        trip_id: TripPath(
            trip_id=trip_id,
            route_id=signature[0],
            direction_id=signature[1],
            stops=stops_by_pattern[signature],
        )
        for trip_id, signature in trip_signatures.items()
    }
    return build_transit_topology(
        feed_label=feed_label,
        stops=stops,
        route_labels={route_id: route.label() for route_id, route in routes.items()},
        route_types={route_id: route.route_type for route_id, route in routes.items()},
        ride_edges=ride_edges,
        transfer_edges=transfer_edges,
        trip_paths=trip_paths,
    )


def _nearby_stop_pairs(
    stops: Mapping[str, GTFSStop],
    served_stop_ids: Iterable[str],
    maximum_distance_meters: float,
) -> Iterator[Tuple[str, str, float]]:
    """Yield bounded pairs using an Earth-centered 3-D spatial hash."""

    grid: Dict[Tuple[int, int, int], list[str]] = {}
    for stop_id in sorted(served_stop_ids):
        stop = stops[stop_id]
        point = _cartesian_point(stop)
        if point is None:
            continue
        cell = tuple(math.floor(value / maximum_distance_meters) for value in point)
        candidates = []
        for x_offset in (-1, 0, 1):
            for y_offset in (-1, 0, 1):
                for z_offset in (-1, 0, 1):
                    candidates.extend(
                        grid.get(
                            (cell[0] + x_offset, cell[1] + y_offset, cell[2] + z_offset),
                            (),
                        )
                    )
        for other_id in sorted(candidates):
            distance = _stop_distance_meters(stops[other_id], stop)
            if distance is not None and distance <= maximum_distance_meters:
                yield other_id, stop_id, distance
        grid.setdefault(cell, []).append(stop_id)


def _cartesian_point(stop: GTFSStop) -> Optional[Tuple[float, float, float]]:
    if stop.stop_lat is None or stop.stop_lon is None:
        return None
    latitude = math.radians(stop.stop_lat)
    longitude = math.radians(stop.stop_lon)
    latitude_cosine = math.cos(latitude)
    return (
        EARTH_RADIUS_METERS * latitude_cosine * math.cos(longitude),
        EARTH_RADIUS_METERS * latitude_cosine * math.sin(longitude),
        EARTH_RADIUS_METERS * math.sin(latitude),
    )


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


def _share_parent_station(first: GTFSStop, second: GTFSStop) -> bool:
    return bool(first.parent_station and first.parent_station == second.parent_station)


def _stop_distance_meters(first: GTFSStop, second: GTFSStop) -> Optional[float]:
    if (
        first.stop_lat is None
        or first.stop_lon is None
        or second.stop_lat is None
        or second.stop_lon is None
    ):
        return None
    latitude_one = math.radians(first.stop_lat)
    latitude_two = math.radians(second.stop_lat)
    latitude_delta = latitude_two - latitude_one
    longitude_delta = math.radians(second.stop_lon - first.stop_lon)
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(latitude_one)
        * math.cos(latitude_two)
        * math.sin(longitude_delta / 2) ** 2
    )
    return EARTH_RADIUS_METERS * 2 * math.asin(math.sqrt(min(1.0, max(0.0, haversine))))


def _table_rows(
    feed: _GTFSFeed,
    name: str,
    required_columns: set[str],
    *,
    required: bool,
) -> Iterator[Tuple[Dict[str, str], int]]:
    with feed.open_table(name, required=required) as handle:
        if handle is None:
            return
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or ())
        missing_columns = sorted(required_columns - fieldnames)
        if missing_columns:
            raise TopologyCompileError(
                f"{name} is missing required column(s): {', '.join(missing_columns)}"
            )
        try:
            for line_number, raw_row in enumerate(reader, 2):
                if None in raw_row:
                    raise _row_error(name, line_number, "contains more fields than its header")
                yield {str(key): "" if value is None else str(value) for key, value in raw_row.items()}, line_number
        except csv.Error as exc:
            raise _row_error(name, reader.line_num, f"invalid CSV: {exc}") from exc


def _required_text(row: Mapping[str, str], field: str, table: str, line_number: int) -> str:
    value = _text(row.get(field))
    if not value:
        raise _row_error(table, line_number, f"{field} is required")
    return value


def _required_int(row: Mapping[str, str], field: str, table: str, line_number: int) -> int:
    value = _optional_int(row.get(field), table, line_number, field)
    if value is None:
        raise _row_error(table, line_number, f"{field} is required")
    return value


def _optional_int(
    value: object, table: str, line_number: int, field: str
) -> Optional[int]:
    text = _text(value)
    if not text:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise _row_error(table, line_number, f"{field} must be an integer, got {text!r}") from exc


def _optional_float(
    value: object, table: str, line_number: int, field: str
) -> Optional[float]:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError as exc:
        raise _row_error(table, line_number, f"{field} must be numeric, got {text!r}") from exc
    if not math.isfinite(parsed):
        raise _row_error(table, line_number, f"{field} must be finite")
    return parsed


def _optional_gtfs_time(
    value: object, table: str, line_number: int, field: str
) -> Optional[int]:
    text = _text(value)
    if not text:
        return None
    parsed = parse_gtfs_time_to_seconds(text)
    if parsed is None or parsed < 0:
        raise _row_error(table, line_number, f"{field} is not a valid GTFS time: {text!r}")
    return parsed


def _optional_text(value: object) -> Optional[str]:
    text = _text(value)
    return text or None


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _row_error(table: str, line_number: int, message: str) -> TopologyCompileError:
    return TopologyCompileError(f"{table}:{line_number}: {message}")


def _pattern_sort_key(signature: PatternSignature) -> Tuple[Any, ...]:
    return (
        signature[0],
        signature[1] is None,
        -1 if signature[1] is None else signature[1],
        signature[2],
    )


def _ride_edge_sort_key(key: RideEdgeKey) -> Tuple[Any, ...]:
    return (
        key[0],
        key[1] is None,
        -1 if key[1] is None else key[1],
        key[2],
        key[3],
    )


def _validate_options(
    *,
    max_nearby_walk_meters: float,
    walking_speed_mps: float,
    station_transfer_floor_seconds: int,
) -> None:
    if not math.isfinite(max_nearby_walk_meters) or max_nearby_walk_meters < 0:
        raise TopologyCompileError("max_nearby_walk_meters must be finite and non-negative")
    if not math.isfinite(walking_speed_mps) or walking_speed_mps <= 0:
        raise TopologyCompileError("walking_speed_mps must be finite and positive")
    if station_transfer_floor_seconds < 0:
        raise TopologyCompileError("station_transfer_floor_seconds must be non-negative")


def _default_feed_label(source: Path) -> str:
    return source.name if source.is_dir() else source.stem


def _atomic_save(compiled: CompiledTopology, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    suffix = ".gz" if destination.suffix == ".gz" else ".json"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=suffix, dir=destination.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        save_transit_topology(
            compiled.topology,
            temporary_path,
            metadata=compiled.metadata,
        )
        mode = destination.stat().st_mode & 0o777 if destination.exists() else 0o644
        temporary_path.chmod(mode)
        with temporary_path.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        try:
            directory_descriptor = os.open(destination.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stream a static GTFS zip/directory into a Transit Sentinel topology artifact."
    )
    parser.add_argument("source", help="static GTFS zip archive or directory")
    parser.add_argument("output", help="destination .json or .json.gz artifact")
    parser.add_argument("--feed-label", help="stable feed label stored in the artifact")
    parser.add_argument(
        "--max-nearby-walk-meters",
        type=float,
        default=250.0,
        help="maximum inferred nearby-stop walk (0 disables; default: 250)",
    )
    parser.add_argument(
        "--walking-speed-mps",
        type=float,
        default=1.2,
        help="walking speed used for inferred transfer times (default: 1.2)",
    )
    parser.add_argument(
        "--station-transfer-floor-seconds",
        type=int,
        default=60,
        help="minimum inferred shared-station transfer time (default: 60)",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        compiled = compile_and_save_gtfs_topology(
            args.source,
            args.output,
            feed_label=args.feed_label,
            max_nearby_walk_meters=args.max_nearby_walk_meters,
            walking_speed_mps=args.walking_speed_mps,
            station_transfer_floor_seconds=args.station_transfer_floor_seconds,
        )
    except (OSError, TopologyCompileError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(json.dumps(compiled.metadata, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
