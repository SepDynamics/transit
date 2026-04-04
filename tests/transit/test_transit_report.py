import json
from io import BytesIO
from zipfile import ZipFile

from scripts.transit.report import build_archive_report


def test_build_archive_report_summarizes_corridor_history(tmp_path):
    archive_dir = tmp_path / "archive" / "2026" / "04" / "04" / "010000Z"
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / "MBTA_GTFS.zip").write_bytes(_build_static_feed())
    (archive_dir / "VehiclePositions_enhanced.json").write_text(_vehicle_positions_payload(), encoding="utf-8")
    (archive_dir / "TripUpdates_enhanced.json").write_text(_trip_updates_payload(), encoding="utf-8")
    (archive_dir / "Alerts_enhanced.json").write_text('{"header":{"timestamp":1710000100},"entity":[]}', encoding="utf-8")
    (archive_dir / "manifest.json").write_text(
        json.dumps({"agency": "Transit Test", "timestamp_ms": 1_710_000_100_000}),
        encoding="utf-8",
    )

    payload = build_archive_report(tmp_path)

    assert payload["snapshot_count"] == 1
    assert payload["corridors"][0]["route_id"] == "Red"
    assert payload["corridors"][0]["incident_snapshot_count"] == 1
    assert payload["corridors"][0]["top_action"] == "hold"


def test_build_archive_report_uses_manifest_feed_paths_for_static_gtfs(tmp_path):
    archived_static = tmp_path / "archive" / "2026" / "04" / "04" / "000000Z"
    archived_static.mkdir(parents=True, exist_ok=True)
    (archived_static / "MBTA_GTFS.zip").write_bytes(_build_static_feed())

    archive_dir = tmp_path / "archive" / "2026" / "04" / "04" / "010000Z"
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / "VehiclePositions_enhanced.json").write_text(_vehicle_positions_payload(), encoding="utf-8")
    (archive_dir / "TripUpdates_enhanced.json").write_text(_trip_updates_payload(), encoding="utf-8")
    (archive_dir / "Alerts_enhanced.json").write_text('{"header":{"timestamp":1710000100},"entity":[]}', encoding="utf-8")
    (archive_dir / "manifest.json").write_text(
        json.dumps(
            {
                "agency": "Transit Test",
                "timestamp_ms": 1_710_000_100_000,
                "snapshot_path": "archive/2026/04/04/010000Z",
                "feeds": [
                    {"name": "static_gtfs", "path": "archive/2026/04/04/000000Z/MBTA_GTFS.zip"},
                    {"name": "vehicle_positions", "path": "archive/2026/04/04/010000Z/VehiclePositions_enhanced.json"},
                    {"name": "trip_updates", "path": "archive/2026/04/04/010000Z/TripUpdates_enhanced.json"},
                    {"name": "alerts", "path": "archive/2026/04/04/010000Z/Alerts_enhanced.json"},
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = build_archive_report(tmp_path)

    assert payload["snapshot_count"] == 1
    assert payload["corridors"][0]["route_id"] == "Red"


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
    {"id": "veh-1811", "vehicle": {"trip": {"route_id": "Red", "trip_id": "red-1", "direction_id": 0, "start_date": "20240309"}, "vehicle": {"id": "1811", "label": "1811"}, "position": {"latitude": 42.396, "longitude": -71.122}, "stop_id": "place-davis", "current_status": "IN_TRANSIT_TO", "current_stop_sequence": 2, "timestamp": 1710000100}},
    {"id": "veh-1812", "vehicle": {"trip": {"route_id": "Red", "trip_id": "red-2", "direction_id": 0, "start_date": "20240309"}, "vehicle": {"id": "1812", "label": "1812"}, "position": {"latitude": 42.397, "longitude": -71.121}, "stop_id": "place-davis", "current_status": "IN_TRANSIT_TO", "current_stop_sequence": 2, "timestamp": 1710000100}},
    {"id": "veh-1813", "vehicle": {"trip": {"route_id": "Red", "trip_id": "red-3", "direction_id": 0, "start_date": "20240309"}, "vehicle": {"id": "1813", "label": "1813"}, "position": {"latitude": 42.398, "longitude": -71.120}, "stop_id": "place-davis", "current_status": "IN_TRANSIT_TO", "current_stop_sequence": 3, "timestamp": 1710000100}}
  ]
}"""


def _trip_updates_payload() -> str:
    return """{
  "header": {"timestamp": 1710000100},
  "entity": [
    {"id": "trip-1", "trip_update": {"trip": {"route_id": "Red", "trip_id": "red-1", "direction_id": 0, "start_date": "20240309"}, "vehicle": {"id": "1811"}, "stop_time_update": [{"stop_id": "place-davis", "stop_sequence": 2, "arrival": {"time": 1709971740}, "departure": {"time": 1709971800}}]}},
    {"id": "trip-2", "trip_update": {"trip": {"route_id": "Red", "trip_id": "red-2", "direction_id": 0, "start_date": "20240309"}, "vehicle": {"id": "1812"}, "stop_time_update": [{"stop_id": "place-davis", "stop_sequence": 2, "arrival": {"time": 1709972160}, "departure": {"time": 1709972220}}]}},
    {"id": "trip-3", "trip_update": {"trip": {"route_id": "Red", "trip_id": "red-3", "direction_id": 0, "start_date": "20240309"}, "vehicle": {"id": "1813"}, "stop_time_update": [{"stop_id": "place-davis", "stop_sequence": 2, "arrival": {"time": 1709972220}, "departure": {"time": 1709972280}}]}}
  ]
}"""
