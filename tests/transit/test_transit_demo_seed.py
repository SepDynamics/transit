import json
from io import BytesIO
from zipfile import ZipFile

from scripts.transit.demo_seed import TransitDemoSeedConfig, TransitDemoSeedService
from scripts.transit.store import TransitStore


def test_transit_demo_seed_service_populates_live_and_replay_views(tmp_path, valkey_url):
    live_case_pack = _write_case_pack(
        tmp_path / "case-packs" / "mbta-live-demo",
        case_pack_id="mbta-live-demo",
        route_id="Red",
        route_label="Red Line",
        timestamp_ms=1_710_000_100_000,
        delay_seconds=240,
    )
    replay_case_pack = _write_case_pack(
        tmp_path / "case-packs" / "mbta-replay-demo",
        case_pack_id="mbta-replay-demo",
        route_id="Orange",
        route_label="Orange Line",
        timestamp_ms=1_710_000_400_000,
        delay_seconds=300,
    )

    status = TransitDemoSeedService(
        TransitDemoSeedConfig(
            redis_url=valkey_url,
            live_case_pack_root=live_case_pack,
            live_snapshot_dir=None,
            live_archive_root=None,
            replay_case_pack_roots=[live_case_pack, replay_case_pack],
            replay_archive_roots=[],
            replay_window_minutes=60,
            replay_max_snapshots=10,
            history_retention=120,
            stale_after_seconds=90,
            clear_store=True,
            trace_prefix="demo",
            output_path=None,
        )
    ).run_once()

    store = TransitStore(valkey_url)
    live_entities = store.entities(scope="live")
    replay_entities = store.entities(scope="replay", trace_id="demo-mbta-replay-demo")
    sources = store.sources()
    ingest_status = store.read_status("ops:transit_ingest_status")
    demo_status = store.read_status("ops:transit_demo_seed_status")

    assert status["status"] == "ok"
    assert status["live_seeded"] is True
    assert status["live_seed"]["case_pack_id"] == "mbta-live-demo"
    assert status["replay_trace_count"] == 2
    assert sources["available"] == {"live": True, "replay": True}
    assert set(sources["trace_ids"]) == {"demo-mbta-live-demo", "demo-mbta-replay-demo"}
    assert live_entities["vehicles"][0]["route_id"] == "Red"
    assert replay_entities["vehicles"][0]["route_id"] == "Orange"
    assert ingest_status["archive_manifest"]["seed_mode"] == "demo_live"
    assert ingest_status["archive_manifest"]["case_pack_id"] == "mbta-live-demo"
    assert demo_status["replay_trace_count"] == 2


def test_transit_demo_seed_service_prefers_archive_windows_for_live_and_replay(
    tmp_path, valkey_url
):
    archive_root = tmp_path / "feeds" / "mbta"
    _write_archive_snapshot(
        archive_root / "archive" / "2026" / "04" / "07" / "120000Z",
        route_id="Red",
        route_label="Red Line",
        timestamp_ms=1_775_563_200_000,
        delay_seconds=180,
    )
    _write_archive_snapshot(
        archive_root / "archive" / "2026" / "04" / "07" / "121000Z",
        route_id="Orange",
        route_label="Orange Line",
        timestamp_ms=1_775_563_800_000,
        delay_seconds=240,
    )

    status = TransitDemoSeedService(
        TransitDemoSeedConfig(
            redis_url=valkey_url,
            live_case_pack_root=None,
            live_snapshot_dir=None,
            live_archive_root=archive_root,
            replay_case_pack_roots=[],
            replay_archive_roots=[archive_root],
            replay_window_minutes=30,
            replay_max_snapshots=10,
            history_retention=120,
            stale_after_seconds=90,
            clear_store=True,
            trace_prefix="demo",
            output_path=None,
        )
    ).run_once()

    store = TransitStore(valkey_url)
    live_entities = store.entities(scope="live")
    replay_entities = store.entities(scope="replay", trace_id="demo-mbta-recent")

    assert status["live_seeded"] is True
    assert status["live_seed"]["source_type"] == "archive_snapshot"
    assert status["live_seed"]["archive_root"] == str(archive_root)
    assert status["replay_traces"][0]["source_type"] == "archive_window"
    assert status["replay_traces"][0]["snapshot_count"] == 2
    assert live_entities["vehicles"][0]["route_id"] == "Orange"
    assert replay_entities["vehicles"][0]["route_id"] == "Orange"


def _write_case_pack(
    case_pack_root,
    *,
    case_pack_id: str,
    route_id: str,
    route_label: str,
    timestamp_ms: int,
    delay_seconds: int,
):
    case_pack_root.mkdir(parents=True, exist_ok=True)
    (case_pack_root / "case_pack.json").write_text(
        json.dumps(
            {
                "agency_keys": ["mbta"],
                "case_pack_id": case_pack_id,
                "category": "rail_delay",
                "city_key": "boston",
                "city_name": "Boston",
                "event_key": "weekday-rail-disruption",
                "event_name": route_label,
            }
        ),
        encoding="utf-8",
    )
    snapshot_dir = case_pack_root / "archive" / "2026" / "04" / "04" / "010000Z"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "MBTA_GTFS.zip").write_bytes(_build_static_feed(route_id=route_id, route_label=route_label))
    (snapshot_dir / "VehiclePositions_enhanced.json").write_text(
        _vehicle_positions_payload(route_id=route_id, timestamp_ms=timestamp_ms, delay_seconds=delay_seconds),
        encoding="utf-8",
    )
    (snapshot_dir / "TripUpdates_enhanced.json").write_text(
        _trip_updates_payload(route_id=route_id, timestamp_ms=timestamp_ms, delay_seconds=delay_seconds),
        encoding="utf-8",
    )
    (snapshot_dir / "Alerts_enhanced.json").write_text('{"header":{"timestamp":1710000100},"entity":[]}', encoding="utf-8")
    (snapshot_dir / "manifest.json").write_text(
        json.dumps(
            {
                "agency": "MBTA",
                "agency_key": "mbta",
                "timestamp_ms": timestamp_ms,
                "snapshot_path": str(snapshot_dir.relative_to(case_pack_root)),
            }
        ),
        encoding="utf-8",
    )
    return case_pack_root


def _write_archive_snapshot(
    snapshot_dir,
    *,
    route_id: str,
    route_label: str,
    timestamp_ms: int,
    delay_seconds: int,
) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "MBTA_GTFS.zip").write_bytes(
        _build_static_feed(route_id=route_id, route_label=route_label)
    )
    (snapshot_dir / "VehiclePositions_enhanced.json").write_text(
        _vehicle_positions_payload(
            route_id=route_id,
            timestamp_ms=timestamp_ms,
            delay_seconds=delay_seconds,
        ),
        encoding="utf-8",
    )
    (snapshot_dir / "TripUpdates_enhanced.json").write_text(
        _trip_updates_payload(
            route_id=route_id,
            timestamp_ms=timestamp_ms,
            delay_seconds=delay_seconds,
        ),
        encoding="utf-8",
    )
    (snapshot_dir / "Alerts_enhanced.json").write_text(
        '{"header":{"timestamp":1710000100},"entity":[]}', encoding="utf-8"
    )
    (snapshot_dir / "manifest.json").write_text(
        json.dumps(
            {
                "agency": "MBTA",
                "agency_key": "mbta",
                "timestamp_ms": timestamp_ms,
                "snapshot_path": str(snapshot_dir.relative_to(snapshot_dir.parents[4])),
            }
        ),
        encoding="utf-8",
    )


def _build_static_feed(*, route_id: str, route_label: str) -> bytes:
    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr(
            "routes.txt",
            "route_id,route_short_name,route_long_name,route_type\n"
            f"{route_id},{route_id},{route_label},1\n",
        )
        archive.writestr(
            "trips.txt",
            "route_id,service_id,trip_id,trip_headsign,direction_id,shape_id\n"
            f"{route_id},WKD,{route_id.lower()}-1,Alewife,0,{route_id.lower()}-shape-0\n"
            f"{route_id},WKD,{route_id.lower()}-2,Alewife,0,{route_id.lower()}-shape-0\n"
            f"{route_id},WKD,{route_id.lower()}-3,Alewife,0,{route_id.lower()}-shape-0\n",
        )
        archive.writestr(
            "stops.txt",
            "stop_id,stop_name,stop_lat,stop_lon\n"
            "place-alfcl,Alewife,42.395428,-71.142483\n"
            "place-davis,Davis,42.39674,-71.121815\n",
        )
        archive.writestr(
            "stop_times.txt",
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            f"{route_id.lower()}-1,08:00:00,08:00:00,place-alfcl,1\n"
            f"{route_id.lower()}-1,08:05:00,08:05:00,place-davis,2\n"
            f"{route_id.lower()}-2,08:08:00,08:08:00,place-alfcl,1\n"
            f"{route_id.lower()}-2,08:13:00,08:13:00,place-davis,2\n"
            f"{route_id.lower()}-3,08:16:00,08:16:00,place-alfcl,1\n"
            f"{route_id.lower()}-3,08:21:00,08:21:00,place-davis,2\n",
        )
        archive.writestr(
            "shapes.txt",
            "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
            f"{route_id.lower()}-shape-0,42.395428,-71.142483,1\n"
            f"{route_id.lower()}-shape-0,42.396740,-71.121815,2\n",
        )
    return payload.getvalue()


def _vehicle_positions_payload(*, route_id: str, timestamp_ms: int, delay_seconds: int) -> str:
    route_slug = route_id.lower()
    timestamp_seconds = int(timestamp_ms / 1000)
    return """{
  "header": {"timestamp": %d},
  "entity": [
    {"id": "veh-1811", "vehicle": {"trip": {"route_id": "%s", "trip_id": "%s-1", "direction_id": 0, "start_date": "20240309"}, "vehicle": {"id": "1811", "label": "1811"}, "position": {"latitude": 42.396, "longitude": -71.122}, "stop_id": "place-davis", "current_status": "IN_TRANSIT_TO", "current_stop_sequence": 2, "delay": %d, "timestamp": %d}}
  ]
}""" % (timestamp_seconds, route_id, route_slug, delay_seconds, timestamp_seconds)


def _trip_updates_payload(*, route_id: str, timestamp_ms: int, delay_seconds: int) -> str:
    route_slug = route_id.lower()
    scheduled_arrival = 1_709_971_200 + delay_seconds
    scheduled_departure = scheduled_arrival + 60
    return """{
  "header": {"timestamp": %d},
  "entity": [
    {"id": "trip-1", "trip_update": {"trip": {"route_id": "%s", "trip_id": "%s-1", "direction_id": 0, "start_date": "20240309"}, "vehicle": {"id": "1811"}, "stop_time_update": [{"stop_id": "place-davis", "stop_sequence": 2, "arrival": {"time": %d}, "departure": {"time": %d}}]}}
  ]
}""" % (int(timestamp_ms / 1000), route_id, route_slug, scheduled_arrival, scheduled_departure)
