import json
from io import BytesIO
from zipfile import ZipFile

from scripts.transit.replay import TransitReplayConfig, TransitReplayService
from scripts.transit.store import TransitStore


def test_transit_replay_service_imports_archived_snapshots_as_trace(tmp_path, valkey_url):
    archive_root = tmp_path / "mbta"
    _write_snapshot(
        archive_root / "archive" / "2024" / "03" / "09" / "010000Z",
        timestamp_ms=1_710_000_100_000,
        delay_seconds=90,
    )
    _write_snapshot(
        archive_root / "archive" / "2024" / "03" / "09" / "010500Z",
        timestamp_ms=1_710_000_400_000,
        delay_seconds=300,
    )

    service = TransitReplayService(
        TransitReplayConfig(
            redis_url=valkey_url,
            archive_root=archive_root,
            trace_id="mbta-proof-case",
            snapshot_dirs=[],
            history_retention=120,
            system_name="MBTA",
            stale_after_seconds=90,
            feed_timezone="America/New_York",
            clear_trace=False,
        )
    )

    status = service.run_once()

    store = TransitStore(valkey_url)
    sources = store.sources()
    replay_entities = store.entities(scope="replay", trace_id="mbta-proof-case")
    replay_history = store.history("vehicle:1811", scope="replay", trace_id="mbta-proof-case", limit=10)

    assert status["status"] == "ok"
    assert status["snapshot_count"] == 2
    assert status["trace_id"] == "mbta-proof-case"
    assert sources["available"]["replay"] is True
    assert sources["trace_ids"] == ["mbta-proof-case"]
    assert sources["traces"][0]["trace_id"] == "mbta-proof-case"
    assert sources["traces"][0]["snapshot_count"] == 2
    assert sources["traces"][0]["latest_snapshot_timestamp_ms"] == 1_710_000_400_000
    assert replay_entities["vehicles"][0]["source"] == "replay"
    assert replay_entities["vehicles"][0]["delay_seconds"] == 300
    assert replay_entities["vehicles"][0]["observation"]["trace_id"] == "mbta-proof-case"
    assert [row["delay_seconds"] for row in replay_history["observations"]] == [90, 300]
    assert {row["source"] for row in replay_history["regimes"]} == {"replay"}


def _write_snapshot(snapshot_dir, *, timestamp_ms: int, delay_seconds: int) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "MBTA_GTFS.zip").write_bytes(_build_static_feed())
    (snapshot_dir / "VehiclePositions_enhanced.json").write_text(
        _vehicle_positions_payload(timestamp_ms, delay_seconds),
        encoding="utf-8",
    )
    (snapshot_dir / "TripUpdates_enhanced.json").write_text(_trip_updates_payload(timestamp_ms, delay_seconds), encoding="utf-8")
    (snapshot_dir / "Alerts_enhanced.json").write_text('{"header":{"timestamp":1710000100},"entity":[]}', encoding="utf-8")
    (snapshot_dir / "manifest.json").write_text(
        json.dumps(
            {
                "agency": "MBTA",
                "timestamp_ms": timestamp_ms,
                "snapshot_path": str(snapshot_dir.relative_to(snapshot_dir.parents[4])),
            }
        ),
        encoding="utf-8",
    )


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


def _vehicle_positions_payload(timestamp_ms: int, delay_seconds: int) -> str:
    timestamp_seconds = int(timestamp_ms / 1000)
    return """{
  "header": {"timestamp": %d},
  "entity": [
    {"id": "veh-1811", "vehicle": {"trip": {"route_id": "Red", "trip_id": "red-1", "direction_id": 0, "start_date": "20240309"}, "vehicle": {"id": "1811", "label": "1811"}, "position": {"latitude": 42.396, "longitude": -71.122}, "stop_id": "place-davis", "current_status": "IN_TRANSIT_TO", "current_stop_sequence": 2, "delay": %d, "timestamp": %d}}
  ]
}""" % (timestamp_seconds, delay_seconds, timestamp_seconds)


def _trip_updates_payload(timestamp_ms: int, delay_seconds: int) -> str:
    scheduled_arrival = 1_709_971_200 + delay_seconds
    scheduled_departure = scheduled_arrival + 60
    return """{
  "header": {"timestamp": %d},
  "entity": [
    {"id": "trip-1", "trip_update": {"trip": {"route_id": "Red", "trip_id": "red-1", "direction_id": 0, "start_date": "20240309"}, "vehicle": {"id": "1811"}, "stop_time_update": [{"stop_id": "place-davis", "stop_sequence": 2, "arrival": {"time": %d}, "departure": {"time": %d}}]}}
  ]
}""" % (int(timestamp_ms / 1000), scheduled_arrival, scheduled_departure)
