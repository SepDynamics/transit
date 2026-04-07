"""Transit agency adapter registry and runtime defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class TransitAgencyAdapter:
    key: str
    system_name: str
    timezone_name: str
    archive_root: str
    static_feed_filename: str
    vehicle_positions_filename: str = "VehiclePositions_enhanced.json"
    trip_updates_filename: str = "TripUpdates_enhanced.json"
    alerts_filename: str = "Alerts_enhanced.json"
    static_url: Optional[str] = None
    vehicle_positions_url: Optional[str] = None
    trip_updates_url: Optional[str] = None
    alerts_url: Optional[str] = None
    source_name: Optional[str] = None
    # WebSocket base URL for agencies that publish realtime via WS
    # (e.g. LA Metro: wss://api.metro.net/ws/{agency_id}/{endpoint}/{route_codes})
    websocket_base_url: Optional[str] = None
    # agency_id value used in websocket / REST API paths (may differ from adapter key)
    api_agency_id: Optional[str] = None
    # When True the archiver uses the websocket path instead of HTTP polling
    # for vehicle_positions and trip_updates feeds.
    realtime_via_websocket: bool = False
    # Route codes to subscribe to on the websocket (empty = all routes)
    websocket_route_codes: List[str] = field(default_factory=list)

    def archive_root_path(self) -> Path:
        return Path(self.archive_root)

    def current_dir_path(self) -> Path:
        return self.archive_root_path() / "current"

    def default_feed_paths(self) -> Dict[str, str]:
        current_dir = self.current_dir_path()
        return {
            "static_gtfs": str(current_dir / self.static_feed_filename),
            "vehicle_positions": str(current_dir / self.vehicle_positions_filename),
            "trip_updates": str(current_dir / self.trip_updates_filename),
            "alerts": str(current_dir / self.alerts_filename),
        }

    def configured_feed_urls(self) -> Dict[str, str]:
        configured: Dict[str, str] = {}
        if self.static_url:
            configured["static_gtfs"] = self.static_url
        if self.vehicle_positions_url:
            configured["vehicle_positions"] = self.vehicle_positions_url
        if self.trip_updates_url:
            configured["trip_updates"] = self.trip_updates_url
        if self.alerts_url:
            configured["alerts"] = self.alerts_url
        return configured

    def vehicle_positions_ws_url(self) -> Optional[str]:
        """WebSocket URL for vehicle_positions stream, if configured."""
        if not self.websocket_base_url or not self.api_agency_id:
            return None
        route_codes = (
            ",".join(self.websocket_route_codes)
            if self.websocket_route_codes
            else "all"
        )
        return f"{self.websocket_base_url}/{self.api_agency_id}/vehicle_positions/{route_codes}"

    def trip_updates_ws_url(self) -> Optional[str]:
        """WebSocket URL for trip_updates stream, if configured."""
        if not self.websocket_base_url or not self.api_agency_id:
            return None
        route_codes = (
            ",".join(self.websocket_route_codes)
            if self.websocket_route_codes
            else "all"
        )
        return (
            f"{self.websocket_base_url}/{self.api_agency_id}/trip_updates/{route_codes}"
        )


TRANSIT_AGENCY_ADAPTERS: Dict[str, TransitAgencyAdapter] = {
    "mbta": TransitAgencyAdapter(
        key="mbta",
        system_name="MBTA",
        timezone_name="America/New_York",
        archive_root="data/feeds/mbta",
        static_feed_filename="MBTA_GTFS.zip",
        static_url="https://cdn.mbta.com/MBTA_GTFS.zip",
        vehicle_positions_url="https://cdn.mbta.com/realtime/VehiclePositions_enhanced.json",
        trip_updates_url="https://cdn.mbta.com/realtime/TripUpdates_enhanced.json",
        alerts_url="https://cdn.mbta.com/realtime/Alerts_enhanced.json",
        source_name="Massachusetts Bay Transportation Authority",
    ),
    "lametro-rail": TransitAgencyAdapter(
        key="lametro-rail",
        system_name="LA Metro Rail",
        timezone_name="America/Los_Angeles",
        archive_root="data/feeds/lametro/rail",
        static_feed_filename="gtfs_rail.zip",
        static_url="https://gitlab.com/LACMTA/gtfs_rail/raw/master/gtfs_rail.zip",
        source_name="Los Angeles County Metropolitan Transportation Authority",
        # LA Metro Rail publishes vehicle positions and trip updates via WebSocket.
        # Agency ID in the Metro API is LACMTA_Rail.
        # Alert feed: no documented public alert endpoint as of April 2026;
        # use canceled_service endpoints as a partial substitute.
        websocket_base_url="wss://api.metro.net/ws",
        api_agency_id="LACMTA_Rail",
        realtime_via_websocket=True,
    ),
    "lametro-bus": TransitAgencyAdapter(
        key="lametro-bus",
        system_name="LA Metro Bus",
        timezone_name="America/Los_Angeles",
        archive_root="data/feeds/lametro/bus",
        static_feed_filename="gtfs_bus.zip",
        static_url="https://gitlab.com/LACMTA/gtfs_bus/raw/master/gtfs_bus.zip",
        source_name="Los Angeles County Metropolitan Transportation Authority",
        # LA Metro Bus publishes vehicle positions and trip updates via WebSocket.
        # Agency ID in the Metro API is LACMTA.
        websocket_base_url="wss://api.metro.net/ws",
        api_agency_id="LACMTA",
        realtime_via_websocket=True,
    ),
}


def get_transit_agency_adapter(key: str | None) -> TransitAgencyAdapter:
    adapter_key = str(key or "mbta").strip().lower()
    if adapter_key not in TRANSIT_AGENCY_ADAPTERS:
        supported = ", ".join(sorted(TRANSIT_AGENCY_ADAPTERS))
        raise ValueError(
            f"unsupported transit agency adapter: {adapter_key} (supported: {supported})"
        )
    return TRANSIT_AGENCY_ADAPTERS[adapter_key]


def default_transit_agency_key() -> str:
    return "mbta"
