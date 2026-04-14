"""Transit-native data models used by the scaffold refactor."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional
from zoneinfo import ZoneInfo


def parse_gtfs_time_to_seconds(value: str | None) -> Optional[int]:
    if not value:
        return None
    parts = value.split(":")
    if len(parts) != 3:
        return None
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = int(parts[2])
    except ValueError:
        return None
    if minutes < 0 or minutes >= 60 or seconds < 0 or seconds >= 60:
        return None
    return (hours * 3600) + (minutes * 60) + seconds


def _string_or_default(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value)
    return text if text else default


def _optional_string(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int_or_default(value: Any, default: int = 0) -> int:
    parsed = _optional_int(value)
    return default if parsed is None else parsed


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float_or_default(value: Any, default: float = 0.0) -> float:
    parsed = _optional_float(value)
    return default if parsed is None else parsed


def _dict_or_empty(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list_of_strings(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item not in (None, "")]


def _list_of_dicts(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


@dataclass
class GTFSRoute:
    route_id: str
    route_short_name: str = ""
    route_long_name: str = ""
    route_desc: str = ""
    route_type: Optional[int] = None
    agency_id: Optional[str] = None

    def label(self) -> str:
        if self.route_short_name and self.route_long_name:
            return f"{self.route_short_name} {self.route_long_name}"
        if self.route_short_name:
            return self.route_short_name
        if self.route_long_name:
            return self.route_long_name
        return self.route_id


@dataclass
class GTFSTrip:
    trip_id: str
    route_id: str
    service_id: str = ""
    trip_headsign: str = ""
    direction_id: Optional[int] = None
    shape_id: Optional[str] = None
    block_id: Optional[str] = None


@dataclass
class GTFSStop:
    stop_id: str
    stop_name: str
    stop_lat: Optional[float] = None
    stop_lon: Optional[float] = None
    parent_station: Optional[str] = None
    location_type: Optional[int] = None


@dataclass
class GTFSStopTime:
    trip_id: str
    stop_id: str
    stop_sequence: int
    arrival_time: Optional[str] = None
    departure_time: Optional[str] = None

    def arrival_seconds(self) -> Optional[int]:
        return parse_gtfs_time_to_seconds(self.arrival_time)

    def departure_seconds(self) -> Optional[int]:
        return parse_gtfs_time_to_seconds(self.departure_time)


@dataclass
class GTFSCalendarService:
    service_id: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    monday: int = 0
    tuesday: int = 0
    wednesday: int = 0
    thursday: int = 0
    friday: int = 0
    saturday: int = 0
    sunday: int = 0


@dataclass
class GTFSShapePoint:
    shape_id: str
    shape_pt_sequence: int
    shape_pt_lat: float
    shape_pt_lon: float


@dataclass
class GTFSStaticCatalog:
    feed_label: str
    routes: Dict[str, GTFSRoute] = field(default_factory=dict)
    trips: Dict[str, GTFSTrip] = field(default_factory=dict)
    stops: Dict[str, GTFSStop] = field(default_factory=dict)
    stop_times_by_trip: Dict[str, List[GTFSStopTime]] = field(default_factory=dict)
    calendar: Dict[str, GTFSCalendarService] = field(default_factory=dict)
    shapes: Dict[str, List[GTFSShapePoint]] = field(default_factory=dict)
    _active_service_ids_cache: Dict[str, set[str]] = field(default_factory=dict, init=False, repr=False)
    _route_window_cache: Dict[tuple[str, Optional[int], str], Optional[tuple[int, int]]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def route_label(self, route_id: str | None) -> str:
        if not route_id:
            return "Unassigned route"
        route = self.routes.get(route_id)
        return route.label() if route else route_id

    def route_type(self, route_id: str | None) -> Optional[int]:
        if not route_id:
            return None
        route = self.routes.get(route_id)
        return route.route_type if route else None

    def route_mode(self, route_id: str | None) -> str:
        route_type = self.route_type(route_id)
        if route_type == 0:
            return "light_rail"
        if route_type == 1:
            return "subway"
        if route_type == 2:
            return "commuter_rail"
        if route_type == 3:
            return "bus"
        if route_type == 4:
            return "ferry"
        return "other"

    def trip_label(self, trip_id: str | None) -> str:
        if not trip_id:
            return "Unassigned trip"
        trip = self.trips.get(trip_id)
        if not trip:
            return trip_id
        route_name = self.route_label(trip.route_id)
        if trip.trip_headsign:
            return f"{route_name} to {trip.trip_headsign}"
        return f"{route_name} {trip.trip_id}"

    def route_stop_times(self, route_id: str, direction_id: Optional[int] = None) -> List[GTFSStopTime]:
        rows: List[GTFSStopTime] = []
        for trip in self.trips.values():
            if trip.route_id != route_id:
                continue
            if direction_id is not None and trip.direction_id != direction_id:
                continue
            rows.extend(self.stop_times_by_trip.get(trip.trip_id, []))
        rows.sort(key=lambda row: (row.trip_id, row.stop_sequence))
        return rows

    def scheduled_departures(self, route_id: str, direction_id: Optional[int] = None) -> List[int]:
        departures: List[int] = []
        for trip in self.trips.values():
            if trip.route_id != route_id:
                continue
            if direction_id is not None and trip.direction_id != direction_id:
                continue
            stop_times = self.stop_times_by_trip.get(trip.trip_id, [])
            if not stop_times:
                continue
            departure_seconds = stop_times[0].departure_seconds() or stop_times[0].arrival_seconds()
            if departure_seconds is not None:
                departures.append(departure_seconds)
        departures.sort()
        return departures

    def scheduled_headway_seconds(self, route_id: str, direction_id: Optional[int] = None) -> Optional[int]:
        departures = self.scheduled_departures(route_id, direction_id)
        if len(departures) < 2:
            return None
        deltas = [later - earlier for earlier, later in zip(departures, departures[1:]) if later > earlier]
        if not deltas:
            return None
        midpoint = len(deltas) // 2
        sorted_deltas = sorted(deltas)
        if len(sorted_deltas) % 2:
            return sorted_deltas[midpoint]
        return int(round((sorted_deltas[midpoint - 1] + sorted_deltas[midpoint]) / 2.0))

    def trip_route_id(self, trip_id: str | None) -> Optional[str]:
        if not trip_id:
            return None
        trip = self.trips.get(trip_id)
        return trip.route_id if trip else None

    def trip_direction_id(self, trip_id: str | None) -> Optional[int]:
        if not trip_id:
            return None
        trip = self.trips.get(trip_id)
        return trip.direction_id if trip else None

    def trip_headsign(self, trip_id: str | None) -> Optional[str]:
        if not trip_id:
            return None
        trip = self.trips.get(trip_id)
        if not trip or not trip.trip_headsign:
            return None
        return trip.trip_headsign

    def route_geometry(
        self, route_id: str | None, direction_id: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        coordinates = self.route_shape_coordinates(route_id, direction_id)
        if len(coordinates) < 2:
            return None
        return {
            "type": "LineString",
            "coordinates": coordinates,
        }

    def route_shape_coordinates(
        self, route_id: str | None, direction_id: Optional[int] = None
    ) -> List[List[float]]:
        if not route_id:
            return []
        shape_counts: Dict[str, int] = {}
        for trip in self.trips.values():
            if trip.route_id != route_id:
                continue
            if direction_id is not None and trip.direction_id != direction_id:
                continue
            if not trip.shape_id or trip.shape_id not in self.shapes:
                continue
            shape_counts[trip.shape_id] = shape_counts.get(trip.shape_id, 0) + 1
        if shape_counts:
            shape_id = max(
                shape_counts,
                key=lambda value: (shape_counts.get(value, 0), len(self.shapes.get(value, []))),
            )
            coordinates = [
                [float(point.shape_pt_lon), float(point.shape_pt_lat)]
                for point in self.shapes.get(shape_id, [])
            ]
            if len(coordinates) >= 2:
                return _dedupe_coordinates(coordinates)
        return self.route_stop_coordinates(route_id, direction_id)

    def route_stop_coordinates(
        self, route_id: str | None, direction_id: Optional[int] = None
    ) -> List[List[float]]:
        if not route_id:
            return []
        candidate_trips = [
            trip
            for trip in self.trips.values()
            if trip.route_id == route_id
            and (direction_id is None or trip.direction_id == direction_id)
        ]
        candidate_trips.sort(key=lambda trip: trip.trip_id)
        for trip in candidate_trips:
            coordinates: List[List[float]] = []
            for stop_time in self.stop_times_by_trip.get(trip.trip_id, []):
                stop = self.stops.get(stop_time.stop_id)
                if not stop or stop.stop_lat is None or stop.stop_lon is None:
                    continue
                coordinates.append([float(stop.stop_lon), float(stop.stop_lat)])
            deduped = _dedupe_coordinates(coordinates)
            if len(deduped) >= 2:
                return deduped
        return []

    def find_stop_time(
        self,
        trip_id: str | None,
        *,
        stop_sequence: Optional[int] = None,
        stop_id: Optional[str] = None,
    ) -> Optional[GTFSStopTime]:
        if not trip_id:
            return None
        for stop_time in self.stop_times_by_trip.get(trip_id, []):
            if stop_sequence is not None and stop_time.stop_sequence == stop_sequence:
                return stop_time
            if stop_id and stop_time.stop_id == stop_id:
                return stop_time
        return None

    def scheduled_epoch_seconds(
        self,
        trip_id: str | None,
        *,
        service_date: Optional[str],
        timezone_name: str = "UTC",
        stop_sequence: Optional[int] = None,
        stop_id: Optional[str] = None,
        event: str = "arrival",
    ) -> Optional[int]:
        if not service_date:
            return None
        stop_time = self.find_stop_time(trip_id, stop_sequence=stop_sequence, stop_id=stop_id)
        if not stop_time:
            return None
        scheduled_seconds = stop_time.arrival_seconds() if event == "arrival" else stop_time.departure_seconds()
        if scheduled_seconds is None:
            scheduled_seconds = stop_time.departure_seconds() if event == "arrival" else stop_time.arrival_seconds()
        if scheduled_seconds is None:
            return None
        try:
            base_date = datetime.strptime(service_date, "%Y%m%d").date()
        except ValueError:
            return None
        day_offset, seconds_of_day = divmod(scheduled_seconds, 24 * 3600)
        scheduled_date = base_date + timedelta(days=day_offset)
        try:
            zone = ZoneInfo(timezone_name)
        except Exception:
            zone = timezone.utc
        midnight = datetime.combine(scheduled_date, datetime.min.time(), tzinfo=zone)
        return int(midnight.timestamp()) + seconds_of_day

    def trip_schedule_window_seconds(self, trip_id: str | None) -> Optional[tuple[int, int]]:
        if not trip_id:
            return None
        stop_times = self.stop_times_by_trip.get(trip_id, [])
        if not stop_times:
            return None
        first_stop = stop_times[0]
        last_stop = stop_times[-1]
        start_seconds = first_stop.departure_seconds() or first_stop.arrival_seconds()
        end_seconds = last_stop.arrival_seconds() or last_stop.departure_seconds()
        if start_seconds is None and end_seconds is None:
            return None
        if start_seconds is None:
            start_seconds = end_seconds
        if end_seconds is None:
            end_seconds = start_seconds
        if start_seconds is None or end_seconds is None:
            return None
        return start_seconds, end_seconds

    def trip_schedule_window_epoch_seconds(
        self,
        trip_id: str | None,
        *,
        service_date: Optional[str],
        timezone_name: str = "UTC",
    ) -> Optional[tuple[int, int]]:
        if not trip_id or not service_date:
            return None
        window_seconds = self.trip_schedule_window_seconds(trip_id)
        if not window_seconds:
            return None
        try:
            base_date = datetime.strptime(service_date, "%Y%m%d").date()
        except ValueError:
            return None
        try:
            zone = ZoneInfo(timezone_name)
        except Exception:
            zone = timezone.utc

        start_seconds, end_seconds = window_seconds
        start_day_offset, start_seconds_of_day = divmod(start_seconds, 24 * 3600)
        end_day_offset, end_seconds_of_day = divmod(end_seconds, 24 * 3600)
        start_date = base_date + timedelta(days=start_day_offset)
        end_date = base_date + timedelta(days=end_day_offset)
        start_midnight = datetime.combine(start_date, datetime.min.time(), tzinfo=zone)
        end_midnight = datetime.combine(end_date, datetime.min.time(), tzinfo=zone)
        return (
            int(start_midnight.timestamp()) + start_seconds_of_day,
            int(end_midnight.timestamp()) + end_seconds_of_day,
        )

    def route_service_window(
        self,
        route_id: str | None,
        *,
        direction_id: Optional[int],
        service_date: date,
    ) -> Optional[tuple[int, int]]:
        if not route_id:
            return None
        cache_key = (route_id, direction_id, service_date.isoformat())
        if cache_key in self._route_window_cache:
            return self._route_window_cache[cache_key]
        if not self.calendar:
            self._route_window_cache[cache_key] = None
            return None
        active_service_ids = self._active_service_ids(service_date)
        if not active_service_ids:
            self._route_window_cache[cache_key] = None
            return None
        start_seconds: List[int] = []
        end_seconds: List[int] = []
        for trip in self.trips.values():
            if trip.route_id != route_id:
                continue
            if direction_id is not None and trip.direction_id != direction_id:
                continue
            if trip.service_id not in active_service_ids:
                continue
            stop_times = self.stop_times_by_trip.get(trip.trip_id, [])
            if not stop_times:
                continue
            first_stop = stop_times[0]
            last_stop = stop_times[-1]
            departure_seconds = first_stop.departure_seconds() or first_stop.arrival_seconds()
            arrival_seconds = last_stop.arrival_seconds() or last_stop.departure_seconds()
            if departure_seconds is None and arrival_seconds is None:
                continue
            start_seconds.append(departure_seconds if departure_seconds is not None else arrival_seconds)
            end_seconds.append(arrival_seconds if arrival_seconds is not None else departure_seconds)
        window = (min(start_seconds), max(end_seconds)) if start_seconds and end_seconds else None
        self._route_window_cache[cache_key] = window
        return window

    def route_is_scheduled_active(
        self,
        route_id: str | None,
        *,
        direction_id: Optional[int],
        timestamp_ms: int,
        timezone_name: str = "UTC",
        padding_seconds: int = 1800,
    ) -> bool:
        if not route_id or route_id in {"network", "unassigned"}:
            return True
        if not self.calendar:
            return True
        try:
            zone = ZoneInfo(timezone_name)
        except Exception:
            zone = timezone.utc
        local_dt = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=zone)
        current_date = local_dt.date()
        seconds_of_day = (local_dt.hour * 3600) + (local_dt.minute * 60) + local_dt.second

        current_window = self.route_service_window(route_id, direction_id=direction_id, service_date=current_date)
        if current_window:
            start_seconds, end_seconds = current_window
            if (start_seconds - padding_seconds) <= seconds_of_day <= (end_seconds + padding_seconds):
                return True

        previous_date = current_date - timedelta(days=1)
        previous_window = self.route_service_window(route_id, direction_id=direction_id, service_date=previous_date)
        if previous_window:
            start_seconds, end_seconds = previous_window
            rollover_seconds = seconds_of_day + (24 * 3600)
            if (start_seconds - padding_seconds) <= rollover_seconds <= (end_seconds + padding_seconds):
                return True
        return False

    def _active_service_ids(self, service_date: date) -> set[str]:
        cache_key = service_date.isoformat()
        if cache_key in self._active_service_ids_cache:
            return self._active_service_ids_cache[cache_key]
        active_ids = {
            service.service_id
            for service in self.calendar.values()
            if service.service_id and _calendar_service_active_on(service, service_date)
        }
        self._active_service_ids_cache[cache_key] = active_ids
        return active_ids


def _calendar_service_active_on(service: GTFSCalendarService, service_date: date) -> bool:
    start_date = _parse_compact_date(service.start_date)
    end_date = _parse_compact_date(service.end_date)
    if start_date and service_date < start_date:
        return False
    if end_date and service_date > end_date:
        return False
    weekday_flags = [
        service.monday,
        service.tuesday,
        service.wednesday,
        service.thursday,
        service.friday,
        service.saturday,
        service.sunday,
    ]
    return bool(weekday_flags[service_date.weekday()])


def _parse_compact_date(value: str | None) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None


@dataclass
class TransitVehicleObservation:
    timestamp_ms: int
    route_id: Optional[str]
    trip_id: Optional[str]
    vehicle_id: str
    vehicle_label: Optional[str] = None
    direction_id: Optional[int] = None
    service_date: Optional[str] = None
    start_time: Optional[str] = None
    stop_id: Optional[str] = None
    current_status: Optional[str] = None
    current_stop_sequence: Optional[int] = None
    occupancy_status: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    bearing: Optional[float] = None
    speed_mps: Optional[float] = None
    delay_seconds: Optional[int] = None
    congestion_level: Optional[str] = None
    source: str = "live"
    collection_source: str = "gtfs_rt"
    trace_id: Optional[str] = None

    def entity_id(self) -> str:
        return f"vehicle:{self.vehicle_id}"

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TransitStopTimeUpdate:
    stop_id: Optional[str]
    stop_sequence: Optional[int]
    arrival_time_unix: Optional[int] = None
    departure_time_unix: Optional[int] = None
    arrival_delay_seconds: Optional[int] = None
    departure_delay_seconds: Optional[int] = None
    schedule_relationship: Optional[str] = None


@dataclass
class TransitTripUpdateObservation:
    timestamp_ms: int
    route_id: Optional[str]
    trip_id: str
    vehicle_id: Optional[str] = None
    direction_id: Optional[int] = None
    service_date: Optional[str] = None
    start_time: Optional[str] = None
    delay_seconds: Optional[int] = None
    stop_time_updates: List[TransitStopTimeUpdate] = field(default_factory=list)
    source: str = "live"
    collection_source: str = "gtfs_rt"
    trace_id: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TransitAlertObservation:
    alert_id: str
    timestamp_ms: int
    effect: Optional[str] = None
    cause: Optional[str] = None
    header_text: Optional[str] = None
    description_text: Optional[str] = None
    route_ids: List[str] = field(default_factory=list)
    stop_ids: List[str] = field(default_factory=list)
    trip_ids: List[str] = field(default_factory=list)
    source: str = "live"
    collection_source: str = "gtfs_rt"
    trace_id: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TransitRealtimeBundle:
    feed_label: str
    feed_timestamp_ms: Optional[int]
    vehicles: List[TransitVehicleObservation] = field(default_factory=list)
    trip_updates: List[TransitTripUpdateObservation] = field(default_factory=list)
    alerts: List[TransitAlertObservation] = field(default_factory=list)
    source: str = "live"
    collection_source: str = "gtfs_rt"
    trace_id: Optional[str] = None

    def latest_timestamp_ms(self) -> Optional[int]:
        timestamps = [value for value in [self.feed_timestamp_ms, *[row.timestamp_ms for row in self.vehicles], *[row.timestamp_ms for row in self.trip_updates], *[row.timestamp_ms for row in self.alerts]] if value is not None]
        return max(timestamps) if timestamps else None

    def to_json(self) -> Dict[str, Any]:
        return {
            "feed_label": self.feed_label,
            "feed_timestamp_ms": self.feed_timestamp_ms,
            "vehicles": [row.to_json() for row in self.vehicles],
            "trip_updates": [row.to_json() for row in self.trip_updates],
            "alerts": [row.to_json() for row in self.alerts],
            "source": self.source,
            "collection_source": self.collection_source,
            "trace_id": self.trace_id,
        }


@dataclass
class TransitEntityRecord:
    entity_id: str
    entity_type: str
    timestamp_ms: int
    label: str
    agency_key: Optional[str] = None
    corridor_id: Optional[str] = None
    route_id: Optional[str] = None
    trip_id: Optional[str] = None
    vehicle_id: Optional[str] = None
    stop_id: Optional[str] = None
    direction_id: Optional[int] = None
    source: str = "live"
    collection_source: str = "gtfs_rt"
    trace_id: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    members: List[str] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TransitRegimeRecord:
    timestamp_ms: int
    entity_id: str
    entity_type: str
    label: str
    route_id: Optional[str]
    regime: str
    hazard: float
    action: str
    scoring_backend: str
    confidence: float
    signature: str
    reasons: List[str]
    provenance: Dict[str, Any]
    metrics: Dict[str, Any]
    agency_key: Optional[str] = None
    corridor_id: Optional[str] = None
    source: str = "live"
    collection_source: str = "gtfs_rt"
    trace_id: Optional[str] = None
    priority_score: int = 0
    priority_label: str = "Monitor"
    regime_label: Optional[str] = None
    action_label: Optional[str] = None
    event_overlays: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TransitRegimeRecord":
        return cls(
            timestamp_ms=_int_or_default(payload.get("timestamp_ms")),
            entity_id=_string_or_default(payload.get("entity_id"), "unknown"),
            entity_type=_string_or_default(payload.get("entity_type"), "corridor"),
            label=_string_or_default(payload.get("label"), "Unknown corridor"),
            agency_key=_optional_string(payload.get("agency_key")),
            corridor_id=_optional_string(payload.get("corridor_id")),
            route_id=_optional_string(payload.get("route_id")),
            regime=_string_or_default(payload.get("regime"), "healthy"),
            hazard=round(_float_or_default(payload.get("hazard")), 4),
            action=_string_or_default(payload.get("action"), "monitor"),
            scoring_backend=_string_or_default(payload.get("scoring_backend"), "heuristic_v1"),
            confidence=round(_float_or_default(payload.get("confidence")), 4),
            signature=_string_or_default(payload.get("signature")),
            reasons=_list_of_strings(payload.get("reasons")),
            provenance=_dict_or_empty(payload.get("provenance")),
            metrics=_dict_or_empty(payload.get("metrics")),
            source=_string_or_default(payload.get("source"), "live"),
            collection_source=_string_or_default(payload.get("collection_source"), "gtfs_rt"),
            trace_id=_optional_string(payload.get("trace_id")),
            priority_score=_int_or_default(payload.get("priority_score")),
            priority_label=_string_or_default(payload.get("priority_label"), "Monitor"),
            regime_label=_optional_string(payload.get("regime_label")),
            action_label=_optional_string(payload.get("action_label")),
            event_overlays=_list_of_dicts(payload.get("event_overlays")),
        )


@dataclass
class TransitIncidentRecord:
    incident_id: str
    timestamp_ms: int
    entity_id: str
    entity_type: str
    label: str
    route_id: Optional[str]
    severity: str
    action: str
    regime: str
    hazard: float
    confidence: float
    summary: str
    recommended_action: str
    reasons: List[str]
    provenance: Dict[str, Any]
    agency_key: Optional[str] = None
    corridor_id: Optional[str] = None
    source: str = "live"
    trace_id: Optional[str] = None
    priority_score: int = 0
    priority_label: str = "Monitor"
    regime_label: Optional[str] = None
    action_label: Optional[str] = None
    event_overlays: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TransitIncidentRecord":
        return cls(
            incident_id=_string_or_default(payload.get("incident_id"), "unknown-incident"),
            timestamp_ms=_int_or_default(payload.get("timestamp_ms")),
            entity_id=_string_or_default(payload.get("entity_id"), "unknown"),
            entity_type=_string_or_default(payload.get("entity_type"), "corridor"),
            label=_string_or_default(payload.get("label"), "Unknown incident"),
            agency_key=_optional_string(payload.get("agency_key")),
            corridor_id=_optional_string(payload.get("corridor_id")),
            route_id=_optional_string(payload.get("route_id")),
            severity=_string_or_default(payload.get("severity"), "info"),
            action=_string_or_default(payload.get("action"), "monitor"),
            regime=_string_or_default(payload.get("regime"), "healthy"),
            hazard=round(_float_or_default(payload.get("hazard")), 4),
            confidence=round(_float_or_default(payload.get("confidence")), 4),
            summary=_string_or_default(payload.get("summary")),
            recommended_action=_string_or_default(payload.get("recommended_action")),
            reasons=_list_of_strings(payload.get("reasons")),
            provenance=_dict_or_empty(payload.get("provenance")),
            source=_string_or_default(payload.get("source"), "live"),
            trace_id=_optional_string(payload.get("trace_id")),
            priority_score=_int_or_default(payload.get("priority_score")),
            priority_label=_string_or_default(payload.get("priority_label"), "Monitor"),
            regime_label=_optional_string(payload.get("regime_label")),
            action_label=_optional_string(payload.get("action_label")),
            event_overlays=_list_of_dicts(payload.get("event_overlays")),
        )


@dataclass
class TransitFeedStatus:
    feed_label: Optional[str] = None
    updated_at: Optional[str] = None
    agency_key: Optional[str] = None
    vehicle_count: int = 0
    trip_update_count: int = 0
    alert_count: int = 0
    collection_source: str = "gtfs_rt"
    status: str = "idle"

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TransitFeedStatus":
        return cls(
            feed_label=_optional_string(payload.get("feed_label")),
            updated_at=_optional_string(payload.get("updated_at")),
            agency_key=_optional_string(payload.get("agency_key")),
            vehicle_count=_int_or_default(payload.get("vehicle_count")),
            trip_update_count=_int_or_default(payload.get("trip_update_count")),
            alert_count=_int_or_default(payload.get("alert_count")),
            collection_source=_string_or_default(payload.get("collection_source"), "gtfs_rt"),
            status=_string_or_default(payload.get("status"), "idle"),
        )


@dataclass
class TransitCorridorSnapshot:
    timestamp_ms: int
    entity_id: str
    label: str
    agency_key: Optional[str] = None
    corridor_id: Optional[str] = None
    route_id: Optional[str] = None
    direction_id: Optional[int] = None
    route_mode: Optional[str] = None
    vehicle_count: int = 0
    median_delay_seconds: int = 0
    scheduled_headway_seconds: Optional[int] = None
    compressed_headway_share: float = 0.0
    avg_delay_seconds: float = 0.0
    top_action: str = "monitor"
    avg_hazard: float = 0.0
    active_alert_count: int = 0
    current_regime: Optional[str] = None
    current_regime_label: Optional[str] = None
    activity_status: Optional[str] = None
    activity_reason: Optional[str] = None
    top_action_label: Optional[str] = None
    priority_score: int = 0
    priority_label: str = "Monitor"
    activity_status_label: Optional[str] = None
    activity_reason_label: Optional[str] = None
    source: str = "live"
    collection_source: str = "gtfs_rt"
    trace_id: Optional[str] = None
    geometry: Optional[Dict[str, Any]] = None
    event_overlays: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TransitCorridorSnapshot":
        return cls(
            timestamp_ms=_int_or_default(payload.get("timestamp_ms")),
            entity_id=_string_or_default(payload.get("entity_id"), "route:unknown:all"),
            label=_string_or_default(payload.get("label"), "Unknown corridor"),
            agency_key=_optional_string(payload.get("agency_key")),
            corridor_id=_optional_string(payload.get("corridor_id")),
            route_id=_optional_string(payload.get("route_id")),
            direction_id=_optional_int(payload.get("direction_id")),
            route_mode=_optional_string(payload.get("route_mode")),
            vehicle_count=_int_or_default(payload.get("vehicle_count")),
            median_delay_seconds=_int_or_default(payload.get("median_delay_seconds")),
            scheduled_headway_seconds=_optional_int(payload.get("scheduled_headway_seconds")),
            compressed_headway_share=round(_float_or_default(payload.get("compressed_headway_share")), 4),
            avg_delay_seconds=round(_float_or_default(payload.get("avg_delay_seconds")), 2),
            top_action=_string_or_default(payload.get("top_action"), "monitor"),
            avg_hazard=round(_float_or_default(payload.get("avg_hazard")), 4),
            active_alert_count=_int_or_default(payload.get("active_alert_count")),
            current_regime=_optional_string(payload.get("current_regime")),
            current_regime_label=_optional_string(payload.get("current_regime_label")),
            activity_status=_optional_string(payload.get("activity_status")),
            activity_reason=_optional_string(payload.get("activity_reason")),
            top_action_label=_optional_string(payload.get("top_action_label")),
            priority_score=_int_or_default(payload.get("priority_score")),
            priority_label=_string_or_default(payload.get("priority_label"), "Monitor"),
            activity_status_label=_optional_string(payload.get("activity_status_label")),
            activity_reason_label=_optional_string(payload.get("activity_reason_label")),
            source=_string_or_default(payload.get("source"), "live"),
            collection_source=_string_or_default(payload.get("collection_source"), "gtfs_rt"),
            trace_id=_optional_string(payload.get("trace_id")),
            geometry=_dict_or_empty(payload.get("geometry")) or None,
            event_overlays=_list_of_dicts(payload.get("event_overlays")),
        )


@dataclass
class TransitVehicleSnapshot:
    entity_id: str
    label: str
    vehicle_id: str
    agency_key: Optional[str] = None
    corridor_id: Optional[str] = None
    route_id: Optional[str] = None
    route_label: Optional[str] = None
    trip_id: Optional[str] = None
    direction_id: Optional[int] = None
    stop_id: Optional[str] = None
    status: Optional[str] = None
    delay_seconds: Optional[int] = None
    occupancy_status: Optional[str] = None
    source: str = "live"
    collection_source: str = "gtfs_rt"
    corridor_entity_id: Optional[str] = None
    regime: Optional[Dict[str, Any]] = None
    observation: Optional[Dict[str, Any]] = None
    event_overlays: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TransitVehicleSnapshot":
        return cls(
            entity_id=_string_or_default(payload.get("entity_id"), "vehicle:unknown"),
            label=_string_or_default(payload.get("label"), "Unknown vehicle"),
            vehicle_id=_string_or_default(payload.get("vehicle_id"), "unknown"),
            agency_key=_optional_string(payload.get("agency_key")),
            corridor_id=_optional_string(payload.get("corridor_id")),
            route_id=_optional_string(payload.get("route_id")),
            route_label=_optional_string(payload.get("route_label")),
            trip_id=_optional_string(payload.get("trip_id")),
            direction_id=_optional_int(payload.get("direction_id")),
            stop_id=_optional_string(payload.get("stop_id")),
            status=_optional_string(payload.get("status")),
            delay_seconds=_optional_int(payload.get("delay_seconds")),
            occupancy_status=_optional_string(payload.get("occupancy_status")),
            source=_string_or_default(payload.get("source"), "live"),
            collection_source=_string_or_default(payload.get("collection_source"), "gtfs_rt"),
            corridor_entity_id=_optional_string(payload.get("corridor_entity_id")),
            regime=_dict_or_empty(payload.get("regime")) or None,
            observation=_dict_or_empty(payload.get("observation")) or None,
            event_overlays=_list_of_dicts(payload.get("event_overlays")),
        )


def _dedupe_coordinates(coordinates: List[List[float]]) -> List[List[float]]:
    deduped: List[List[float]] = []
    for coordinate in coordinates:
        if len(coordinate) != 2:
            continue
        if deduped and deduped[-1] == coordinate:
            continue
        deduped.append([float(coordinate[0]), float(coordinate[1])])
    return deduped


@dataclass
class TransitReplayTrace:
    trace_id: str
    snapshot_count: int = 0
    first_snapshot_path: Optional[str] = None
    latest_snapshot_path: Optional[str] = None
    latest_snapshot_timestamp_ms: Optional[int] = None
    updated_at: Optional[str] = None
    system_name: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TransitReplayTrace":
        return cls(
            trace_id=_string_or_default(payload.get("trace_id"), "unknown-trace"),
            snapshot_count=_int_or_default(payload.get("snapshot_count")),
            first_snapshot_path=_optional_string(payload.get("first_snapshot_path")),
            latest_snapshot_path=_optional_string(payload.get("latest_snapshot_path")),
            latest_snapshot_timestamp_ms=_optional_int(payload.get("latest_snapshot_timestamp_ms")),
            updated_at=_optional_string(payload.get("updated_at")),
            system_name=_optional_string(payload.get("system_name")),
        )
