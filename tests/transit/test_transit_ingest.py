import json
from io import BytesIO
from zipfile import ZipFile

from scripts.transit.domain import TransitRuntimeConfig
from scripts.transit.ingest import TransitIngestConfig, TransitIngestService
from scripts.transit.store import TransitStore


def test_transit_ingest_service_persists_current_snapshot_to_store(tmp_path, valkey_url):
    current_dir = tmp_path / "current"
    current_dir.mkdir(parents=True, exist_ok=True)
    (current_dir / "MBTA_GTFS.zip").write_bytes(_build_static_feed())
    (current_dir / "VehiclePositions_enhanced.json").write_text(_vehicle_positions_payload(), encoding="utf-8")
    (current_dir / "TripUpdates_enhanced.json").write_text(_trip_updates_payload(), encoding="utf-8")
    (current_dir / "Alerts_enhanced.json").write_text('{"header":{"timestamp":1710000100},"entity":[]}', encoding="utf-8")
    (current_dir / "manifest.json").write_text(
        json.dumps({"agency": "MBTA", "snapshot_path": "archive/2026/04/04/010000Z"}),
        encoding="utf-8",
    )

    service = TransitIngestService(
        TransitIngestConfig(
            redis_url=valkey_url,
            interval_seconds=5,
            history_retention=120,
            runtime=TransitRuntimeConfig(
                system_name="MBTA",
                static_feed=str(current_dir / "MBTA_GTFS.zip"),
                vehicle_positions_feed=str(current_dir / "VehiclePositions_enhanced.json"),
                trip_updates_feed=str(current_dir / "TripUpdates_enhanced.json"),
                alerts_feed=str(current_dir / "Alerts_enhanced.json"),
                feed_timezone="America/New_York",
            ),
        )
    )

    payload = service.run_once()

    store = TransitStore(valkey_url)
    health = store.health()
    entities = store.entities()
    history = store.history("vehicle:1811")
    trends = store.trends(limit=5, window=12)
    status = store.read_status("ops:transit_ingest_status")

    assert payload["health"]["line_count"] == 1
    assert health["line_count"] == 1
    assert entities["vehicles"][0]["entity_id"] == "vehicle:1811"
    assert history["observations"][0]["vehicle_id"] == "1811"
    assert history["regimes"][0]["entity_id"] == "route:Red:0"
    assert trends["corridors"][0]["entity_id"] == "route:Red:0"
    assert status["status"] == "ok"
    assert status["archive_manifest"]["snapshot_path"] == "archive/2026/04/04/010000Z"


def _build_static_feed() -> bytes:
    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr(
            "routes.txt",
            "route_id,route_short_name,route_long_name,route_type\n"
            "Red,Red,Red Line,1\n",
        )
        archive.writestr(
            "trips.txt",
            "route_id,service_id,trip_id,trip_headsign,direction_id\n"
            "Red,WKD,red-1,Alewife,0\n"
            "Red,WKD,red-2,Alewife,0\n"
            "Red,WKD,red-3,Alewife,0\n",
        )
        archive.writestr(
            "stop_times.txt",
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "red-1,08:00:00,08:00:00,place-alfcl,1\n"
            "red-1,08:05:00,08:05:00,place-davis,2\n"
            "red-2,08:08:00,08:08:00,place-alfcl,1\n"
            "red-2,08:13:00,08:13:00,place-davis,2\n"
            "red-3,08:16:00,08:16:00,place-alfcl,1\n"
            "red-3,08:21:00,08:21:00,place-davis,2\n",
        )
    return payload.getvalue()


def _vehicle_positions_payload() -> str:
    return """{
  "header": {"timestamp": 1710000100},
  "entity": [
    {"id": "veh-1811", "vehicle": {"trip": {"route_id": "Red", "trip_id": "red-1", "direction_id": 0, "start_date": "20240309"}, "vehicle": {"id": "1811", "label": "1811"}, "position": {"latitude": 42.396, "longitude": -71.122}, "stop_id": "place-davis", "current_status": "IN_TRANSIT_TO", "current_stop_sequence": 2, "timestamp": 1710000100}}
  ]
}"""


def _trip_updates_payload() -> str:
    return """{
  "header": {"timestamp": 1710000100},
  "entity": [
    {"id": "trip-1", "trip_update": {"trip": {"route_id": "Red", "trip_id": "red-1", "direction_id": 0, "start_date": "20240309"}, "vehicle": {"id": "1811"}, "stop_time_update": [{"stop_id": "place-davis", "stop_sequence": 2, "arrival": {"time": 1709971740}, "departure": {"time": 1709971800}}]}}
  ]
}"""
