"""Transit agency adapter registry and runtime defaults."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


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
    "lametro": TransitAgencyAdapter(
        key="lametro",
        system_name="LA Metro",
        timezone_name="America/Los_Angeles",
        archive_root="data/feeds/lametro",
        static_feed_filename="lametro_gtfs.zip",
        # Static GTFS: LA Metro official bus + rail feed from GitLab
        static_url="https://gitlab.com/LACMTA/gtfs_bus/raw/master/gtfs_bus.zip",
        # Real-time GTFS-RT via Swiftly (requires Authorization header)
        # Vehicle positions and trip updates support ?format=json;
        # alerts only returns binary protobuf, so no ?format=json.
        vehicle_positions_url="https://api.goswift.ly/real-time/lametro/gtfs-rt-vehicle-positions?format=json",
        trip_updates_url="https://api.goswift.ly/real-time/lametro/gtfs-rt-trip-updates?format=json",
        alerts_url="https://api.goswift.ly/real-time/lametro/gtfs-rt-alerts",
        source_name="Los Angeles County Metropolitan Transportation Authority",
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
