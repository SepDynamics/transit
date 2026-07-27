from scripts.transit.domain import TransitRuntimeConfig, TransitSnapshotService, _incident_summary
from scripts.transit.transit_types import TransitRegimeRecord


def test_transit_snapshot_scores_bunching_and_operator_action(tmp_path, monkeypatch):
    static_zip = tmp_path / "mbta.zip"
    static_zip.write_bytes(_build_static_feed())
    vehicles_path = tmp_path / "vehicles.json"
    trip_updates_path = tmp_path / "trip_updates.json"
    alerts_path = tmp_path / "alerts.json"
    vehicles_path.write_text(_vehicle_positions_payload(), encoding="utf-8")
    trip_updates_path.write_text(_trip_updates_payload(), encoding="utf-8")
    alerts_path.write_text(_alerts_payload(), encoding="utf-8")
    monkeypatch.setattr("time.time", lambda: 1_710_000_160)

    service = TransitSnapshotService(
        TransitRuntimeConfig(
            system_name="Transit Sentinel Test",
            static_feed=str(static_zip),
            vehicle_positions_feed=str(vehicles_path),
            trip_updates_feed=str(trip_updates_path),
            alerts_feed=str(alerts_path),
            stale_after_seconds=120,
        )
    )

    health = service.health()
    entities = service.entities()
    incidents = service.incidents()
    regimes = service.regimes()

    assert health["line_count"] == 1
    assert health["active_line_count"] == 1
    assert health["scheduled_later_line_count"] == 0
    assert health["vehicle_count"] == 3
    assert health["incident_count"] == 1
    assert entities["lines"][0]["route_id"] == "Red"
    assert entities["active_lines"][0]["activity_status"] == "active_now"
    assert entities["lines"][0]["top_action"] == "hold"
    assert entities["lines"][0]["top_action_label"] == "Hold to rebalance"
    assert entities["lines"][0]["current_regime_label"] == "Early bunching"
    assert entities["lines"][0]["priority_score"] >= 60
    assert entities["lines"][0]["priority_label"] == "High"
    assert entities["lines"][0]["geometry"]["type"] == "LineString"
    assert len(entities["lines"][0]["geometry"]["coordinates"]) >= 2
    assert entities["vehicles"][0]["route_label"] == "Red Red Line"
    assert incidents["incidents"][0]["action"] == "hold"
    assert incidents["incidents"][0]["regime"] == "bunching_onset"
    assert incidents["incidents"][0]["action_label"] == "Hold to rebalance"
    assert incidents["incidents"][0]["regime_label"] == "Early bunching"
    assert incidents["incidents"][0]["priority_label"] == "High"
    assert "Recommended action: Hold to rebalance." in incidents["incidents"][0]["summary"]
    assert regimes["regimes"][0]["metrics"]["compressed_headway_share"] >= 0.5
    assert regimes["regimes"][0]["metrics"]["median_delay_seconds"] >= 180
    assert regimes["regimes"][0]["priority_label"] == "High"


def test_incident_summary_does_not_report_zero_delay_as_evidence():
    record = TransitRegimeRecord(
        timestamp_ms=1_710_000_160_000,
        entity_id="route:71:0",
        entity_type="corridor",
        label="71 Watertown Square - Harvard Station",
        route_id="71",
        regime="service_degraded",
        hazard=0.36,
        action="warn_riders",
        scoring_backend="heuristic_v1",
        confidence=0.9,
        signature="test",
        reasons=["service_degraded", "service_alert_active"],
        provenance={},
        metrics={},
    )
    summary = _incident_summary(
        record,
        {
            "median_delay_seconds": 0,
            "vehicle_count": 3,
            "trip_update_count": 4,
            "high_impact_alert_count": 1,
            "active_alert_count": 1,
        },
    )

    assert "no measured delay burden" in summary
    assert "median delay 0s" not in summary


def test_transit_snapshot_derives_delay_from_scheduled_times(tmp_path, monkeypatch):
    static_zip = tmp_path / "mbta.zip"
    static_zip.write_bytes(_build_static_feed())
    vehicles_path = tmp_path / "vehicles.json"
    trip_updates_path = tmp_path / "trip_updates.json"
    alerts_path = tmp_path / "alerts.json"
    vehicles_path.write_text(_vehicle_positions_payload(), encoding="utf-8")
    trip_updates_path.write_text(_trip_updates_predicted_time_payload(), encoding="utf-8")
    alerts_path.write_text('{"header":{"timestamp":1710000100},"entity":[]}', encoding="utf-8")
    monkeypatch.setattr("time.time", lambda: 1_710_000_160)

    service = TransitSnapshotService(
        TransitRuntimeConfig(
            system_name="MBTA",
            static_feed=str(static_zip),
            vehicle_positions_feed=str(vehicles_path),
            trip_updates_feed=str(trip_updates_path),
            alerts_feed=str(alerts_path),
            stale_after_seconds=120,
            feed_timezone="America/New_York",
        )
    )

    snapshot = service.snapshot()
    regimes = snapshot["regimes"]

    assert regimes["regimes"][0]["metrics"]["median_delay_seconds"] >= 180
    assert regimes["regimes"][0]["metrics"]["median_delay_seconds"] < 600
    assert regimes["regimes"][0]["regime"] == "bunching_onset"
    prediction_evidence = snapshot["prediction_evidence"]
    assert prediction_evidence["trip_update_count"] == 3
    assert prediction_evidence["event_count"] == 3
    assert prediction_evidence["feed_timestamp_ms"] == 1_710_000_100_000
    assert all(
        row["arrival_time_source"] == "gtfs_rt_time"
        for row in prediction_evidence["events"]
    )


def test_transit_snapshot_history_links_vehicle_to_corridor_regime(tmp_path, monkeypatch):
    static_zip = tmp_path / "mbta.zip"
    static_zip.write_bytes(_build_static_feed())
    vehicles_path = tmp_path / "vehicles.json"
    trip_updates_path = tmp_path / "trip_updates.json"
    alerts_path = tmp_path / "alerts.json"
    vehicles_path.write_text(_vehicle_positions_payload(), encoding="utf-8")
    trip_updates_path.write_text(_trip_updates_payload(), encoding="utf-8")
    alerts_path.write_text(_alerts_payload(), encoding="utf-8")
    monkeypatch.setattr("time.time", lambda: 1_710_000_160)

    service = TransitSnapshotService(
        TransitRuntimeConfig(
            system_name="Transit Sentinel Test",
            static_feed=str(static_zip),
            vehicle_positions_feed=str(vehicles_path),
            trip_updates_feed=str(trip_updates_path),
            alerts_feed=str(alerts_path),
            stale_after_seconds=120,
        )
    )

    entities = service.entities()
    history = service.history(entity_id=entities["vehicles"][0]["entity_id"])

    assert history["observations"][0]["vehicle_id"] == entities["vehicles"][0]["vehicle_id"]
    assert history["regimes"][0]["entity_id"] == entities["vehicles"][0]["corridor_entity_id"]


def test_transit_snapshot_attaches_agency_corridor_ids_and_event_overlays(tmp_path, monkeypatch):
    static_zip = tmp_path / "mbta.zip"
    static_zip.write_bytes(_build_static_feed())
    vehicles_path = tmp_path / "vehicles.json"
    trip_updates_path = tmp_path / "trip_updates.json"
    alerts_path = tmp_path / "alerts.json"
    overlays_path = tmp_path / "event_overlays.json"
    vehicles_path.write_text(_vehicle_positions_payload(), encoding="utf-8")
    trip_updates_path.write_text(_trip_updates_payload(), encoding="utf-8")
    alerts_path.write_text(_alerts_payload(), encoding="utf-8")
    overlays_path.write_text(
        '{"overlays":[{"overlay_id":"mbta-red-proof","label":"Red Line event","agency_keys":["mbta"],"route_ids":["Red"],"corridor_ids":["corridor:mbta:Red:0"],"event_key":"test-event"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr("time.time", lambda: 1_710_000_160)

    service = TransitSnapshotService(
        TransitRuntimeConfig(
            system_name="Transit Sentinel Test",
            agency_key="mbta",
            static_feed=str(static_zip),
            vehicle_positions_feed=str(vehicles_path),
            trip_updates_feed=str(trip_updates_path),
            alerts_feed=str(alerts_path),
            event_overlays_feed=str(overlays_path),
            stale_after_seconds=120,
        )
    )

    entities = service.entities()
    regimes = service.regimes()
    incidents = service.incidents()

    assert entities["lines"][0]["agency_key"] == "mbta"
    assert entities["lines"][0]["corridor_id"] == "corridor:mbta:Red:0"
    assert entities["lines"][0]["event_overlays"][0]["overlay_id"] == "mbta-red-proof"
    assert entities["vehicles"][0]["corridor_id"] == "corridor:mbta:Red:0"
    assert entities["vehicles"][0]["event_overlays"][0]["overlay_id"] == "mbta-red-proof"
    assert regimes["regimes"][0]["agency_key"] == "mbta"
    assert regimes["regimes"][0]["corridor_id"] == "corridor:mbta:Red:0"
    assert incidents["incidents"][0]["corridor_id"] == "corridor:mbta:Red:0"


def test_transit_snapshot_suppresses_sparse_bus_delay_noise(tmp_path, monkeypatch):
    static_zip = tmp_path / "mbta-bus.zip"
    static_zip.write_bytes(_build_sparse_bus_static_feed())
    vehicles_path = tmp_path / "vehicles.json"
    trip_updates_path = tmp_path / "trip_updates.json"
    alerts_path = tmp_path / "alerts.json"
    vehicles_path.write_text(_sparse_bus_vehicle_positions_payload(), encoding="utf-8")
    trip_updates_path.write_text(_sparse_bus_trip_updates_payload(), encoding="utf-8")
    alerts_path.write_text('{"header":{"timestamp":1710000100},"entity":[]}', encoding="utf-8")
    monkeypatch.setattr("time.time", lambda: 1_710_000_160)

    service = TransitSnapshotService(
        TransitRuntimeConfig(
            system_name="Transit Sentinel Test",
            static_feed=str(static_zip),
            vehicle_positions_feed=str(vehicles_path),
            trip_updates_feed=str(trip_updates_path),
            alerts_feed=str(alerts_path),
            stale_after_seconds=120,
        )
    )

    incidents = service.incidents()
    regimes = service.regimes()

    assert incidents["incidents"] == []
    assert regimes["regimes"][0]["regime"] == "healthy"
    assert regimes["regimes"][0]["metrics"]["route_mode"] == "bus"
    assert regimes["regimes"][0]["metrics"]["low_observation"] is True


def test_transit_snapshot_ignores_facility_alerts_for_sparse_bus_signal(tmp_path, monkeypatch):
    static_zip = tmp_path / "mbta-bus-alerts.zip"
    static_zip.write_bytes(_build_sparse_bus_static_feed())
    vehicles_path = tmp_path / "vehicles.json"
    trip_updates_path = tmp_path / "trip_updates.json"
    alerts_path = tmp_path / "alerts.json"
    vehicles_path.write_text(_sparse_bus_vehicle_positions_payload(), encoding="utf-8")
    trip_updates_path.write_text(_sparse_bus_trip_updates_payload(), encoding="utf-8")
    alerts_path.write_text(_facility_alert_payload(), encoding="utf-8")
    monkeypatch.setattr("time.time", lambda: 1_710_000_160)

    service = TransitSnapshotService(
        TransitRuntimeConfig(
            system_name="MBTA",
            static_feed=str(static_zip),
            vehicle_positions_feed=str(vehicles_path),
            trip_updates_feed=str(trip_updates_path),
            alerts_feed=str(alerts_path),
            stale_after_seconds=120,
            feed_timezone="America/New_York",
        )
    )

    incidents = service.incidents()
    regimes = service.regimes()

    assert incidents["incidents"] == []
    assert regimes["regimes"][0]["regime"] == "healthy"
    assert regimes["regimes"][0]["metrics"]["active_alert_count"] == 0
    assert regimes["regimes"][0]["metrics"]["facility_alert_count"] == 1


def test_transit_snapshot_buckets_facility_only_no_telemetry_route_as_scheduled_later(tmp_path, monkeypatch):
    static_zip = tmp_path / "mbta-bus-facility-only.zip"
    static_zip.write_bytes(_build_sparse_bus_static_feed())
    vehicles_path = tmp_path / "vehicles.json"
    trip_updates_path = tmp_path / "trip_updates.json"
    alerts_path = tmp_path / "alerts.json"
    vehicles_path.write_text('{"header":{"timestamp":1709971500},"entity":[]}', encoding="utf-8")
    trip_updates_path.write_text('{"header":{"timestamp":1709971500},"entity":[]}', encoding="utf-8")
    alerts_path.write_text(_facility_alert_payload(route_id="66", timestamp=1709971500), encoding="utf-8")
    monkeypatch.setattr("time.time", lambda: 1_709_971_500)

    service = TransitSnapshotService(
        TransitRuntimeConfig(
            system_name="MBTA",
            static_feed=str(static_zip),
            vehicle_positions_feed=str(vehicles_path),
            trip_updates_feed=str(trip_updates_path),
            alerts_feed=str(alerts_path),
            stale_after_seconds=120,
            feed_timezone="UTC",
        )
    )

    health = service.health()
    entities = service.entities()
    incidents = service.incidents()
    regimes = service.regimes()

    assert health["line_count"] == 0
    assert health["active_line_count"] == 0
    assert health["scheduled_later_line_count"] == 1
    assert entities["lines"] == []
    assert entities["scheduled_later_lines"][0]["route_id"] == "66"
    assert entities["scheduled_later_lines"][0]["activity_status"] == "scheduled_later"
    assert entities["scheduled_later_lines"][0]["activity_reason"] == "scheduled_no_telemetry"
    assert incidents["incidents"] == []
    assert regimes["regimes"][0]["regime"] == "healthy"
    assert regimes["regimes"][0]["metrics"]["scheduled_service_active"] is True


def test_transit_snapshot_requires_corroboration_before_warn_riders_on_bus(tmp_path, monkeypatch):
    static_zip = tmp_path / "mbta-bus-warn.zip"
    static_zip.write_bytes(_build_sparse_bus_static_feed())
    vehicles_path = tmp_path / "vehicles.json"
    trip_updates_path = tmp_path / "trip_updates.json"
    alerts_path = tmp_path / "alerts.json"
    vehicles_path.write_text(_single_bus_vehicle_positions_payload(), encoding="utf-8")
    trip_updates_path.write_text(_single_bus_trip_updates_payload(), encoding="utf-8")
    alerts_path.write_text(_service_alert_payload(), encoding="utf-8")
    monkeypatch.setattr("time.time", lambda: 1_710_000_160)

    service = TransitSnapshotService(
        TransitRuntimeConfig(
            system_name="MBTA",
            static_feed=str(static_zip),
            vehicle_positions_feed=str(vehicles_path),
            trip_updates_feed=str(trip_updates_path),
            alerts_feed=str(alerts_path),
            stale_after_seconds=120,
            feed_timezone="America/New_York",
        )
    )

    incidents = service.incidents()
    regimes = service.regimes()

    assert regimes["regimes"][0]["regime"] == "service_degraded"
    assert regimes["regimes"][0]["action"] == "monitor"
    assert regimes["regimes"][0]["metrics"]["active_alert_count"] == 1
    assert regimes["regimes"][0]["metrics"]["high_impact_alert_count"] == 1
    assert incidents["incidents"] == []


def test_transit_snapshot_filters_far_future_trip_updates(tmp_path, monkeypatch):
    static_zip = tmp_path / "mbta-future-trip.zip"
    static_zip.write_bytes(_build_future_trip_filter_static_feed())
    vehicles_path = tmp_path / "vehicles.json"
    trip_updates_path = tmp_path / "trip_updates.json"
    alerts_path = tmp_path / "alerts.json"
    vehicles_path.write_text('{"header":{"timestamp":1709989860},"entity":[]}', encoding="utf-8")
    trip_updates_path.write_text(_future_trip_filter_trip_updates_payload(), encoding="utf-8")
    alerts_path.write_text('{"header":{"timestamp":1709989860},"entity":[]}', encoding="utf-8")
    monkeypatch.setattr("time.time", lambda: 1_709_989_860)

    service = TransitSnapshotService(
        TransitRuntimeConfig(
            system_name="MBTA",
            static_feed=str(static_zip),
            vehicle_positions_feed=str(vehicles_path),
            trip_updates_feed=str(trip_updates_path),
            alerts_feed=str(alerts_path),
            stale_after_seconds=120,
            feed_timezone="America/New_York",
        )
    )

    snapshot = service.snapshot()
    incidents = snapshot["incidents"]
    regimes = snapshot["regimes"]

    assert incidents["incidents"] == []
    assert regimes["regimes"][0]["regime"] == "healthy"
    assert regimes["regimes"][0]["metrics"]["trip_update_count"] == 1
    assert regimes["regimes"][0]["metrics"]["median_delay_seconds"] == 60
    assert snapshot["prediction_evidence"]["trip_update_count"] == 1
    assert snapshot["prediction_evidence"]["event_count"] == 1
    assert snapshot["prediction_evidence"]["events"][0]["trip_id"] == "bus-now"


def test_transit_snapshot_dedupes_trip_chain_updates(tmp_path, monkeypatch):
    static_zip = tmp_path / "mbta-trip-chain.zip"
    static_zip.write_bytes(_build_trip_chain_dedupe_static_feed())
    vehicles_path = tmp_path / "vehicles.json"
    trip_updates_path = tmp_path / "trip_updates.json"
    alerts_path = tmp_path / "alerts.json"
    vehicles_path.write_text('{"header":{"timestamp":1709989860},"entity":[]}', encoding="utf-8")
    trip_updates_path.write_text(_trip_chain_dedupe_trip_updates_payload(), encoding="utf-8")
    alerts_path.write_text('{"header":{"timestamp":1709989860},"entity":[]}', encoding="utf-8")
    monkeypatch.setattr("time.time", lambda: 1_709_989_860)

    service = TransitSnapshotService(
        TransitRuntimeConfig(
            system_name="MBTA",
            static_feed=str(static_zip),
            vehicle_positions_feed=str(vehicles_path),
            trip_updates_feed=str(trip_updates_path),
            alerts_feed=str(alerts_path),
            stale_after_seconds=120,
            feed_timezone="America/New_York",
        )
    )

    snapshot = service.snapshot()
    incidents = snapshot["incidents"]
    regimes = snapshot["regimes"]

    assert incidents["incidents"] == []
    assert regimes["regimes"][0]["regime"] == "healthy"
    assert regimes["regimes"][0]["metrics"]["trip_update_count"] == 1
    assert regimes["regimes"][0]["metrics"]["median_delay_seconds"] == 600
    assert snapshot["prediction_evidence"]["trip_update_count"] == 1
    assert snapshot["prediction_evidence"]["event_count"] == 1


def test_transit_snapshot_suppresses_inactive_alert_only_route(tmp_path):
    static_zip = tmp_path / "mbta-inactive.zip"
    static_zip.write_bytes(_build_inactive_route_static_feed())
    vehicles_path = tmp_path / "vehicles.json"
    trip_updates_path = tmp_path / "trip_updates.json"
    alerts_path = tmp_path / "alerts.json"
    vehicles_path.write_text('{"header":{"timestamp":1710021600},"entity":[]}', encoding="utf-8")
    trip_updates_path.write_text('{"header":{"timestamp":1710021600},"entity":[]}', encoding="utf-8")
    alerts_path.write_text(_inactive_route_alert_payload(), encoding="utf-8")

    service = TransitSnapshotService(
        TransitRuntimeConfig(
            system_name="MBTA",
            static_feed=str(static_zip),
            vehicle_positions_feed=str(vehicles_path),
            trip_updates_feed=str(trip_updates_path),
            alerts_feed=str(alerts_path),
            stale_after_seconds=120,
            feed_timezone="America/New_York",
        )
    )

    incidents = service.incidents()
    regimes = service.regimes()

    assert incidents["incidents"] == []
    assert regimes["regimes"][0]["regime"] == "healthy"
    assert regimes["regimes"][0]["metrics"]["scheduled_service_active"] is False


def _build_static_feed() -> bytes:
    from io import BytesIO
    from zipfile import ZipFile

    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr(
            "routes.txt",
            "route_id,route_short_name,route_long_name,route_type\n"
            "Red,Red,Red Line,1\n",
        )
        archive.writestr(
            "trips.txt",
            "route_id,service_id,trip_id,trip_headsign,direction_id,shape_id\n"
            "Red,WKD,red-1,Alewife,0,red-shape-0\n"
            "Red,WKD,red-2,Alewife,0,red-shape-0\n"
            "Red,WKD,red-3,Alewife,0,red-shape-0\n",
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
            "red-1,08:00:00,08:00:00,place-alfcl,1\n"
            "red-1,08:05:00,08:05:00,place-davis,2\n"
            "red-2,08:08:00,08:08:00,place-alfcl,1\n"
            "red-2,08:13:00,08:13:00,place-davis,2\n"
            "red-3,08:16:00,08:16:00,place-alfcl,1\n"
            "red-3,08:21:00,08:21:00,place-davis,2\n",
        )
        archive.writestr(
            "shapes.txt",
            "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
            "red-shape-0,42.395428,-71.142483,1\n"
            "red-shape-0,42.396740,-71.121815,2\n",
        )
    return payload.getvalue()


def _build_sparse_bus_static_feed() -> bytes:
    from io import BytesIO
    from zipfile import ZipFile

    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr(
            "routes.txt",
            "route_id,route_short_name,route_long_name,route_type\n"
            "66,66,Harvard Square - Nubian Station,3\n",
        )
        archive.writestr(
            "trips.txt",
            "route_id,service_id,trip_id,trip_headsign,direction_id\n"
            "66,DAILY,bus-1,Nubian,0\n"
            "66,DAILY,bus-2,Nubian,0\n",
        )
        archive.writestr(
            "stop_times.txt",
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "bus-1,08:00:00,08:00:00,stop-a,1\n"
            "bus-1,08:10:00,08:10:00,stop-b,2\n"
            "bus-2,08:10:00,08:10:00,stop-a,1\n"
            "bus-2,08:20:00,08:20:00,stop-b,2\n",
        )
        archive.writestr(
            "calendar.txt",
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
            "DAILY,1,1,1,1,1,1,1,20240101,20241231\n",
        )
    return payload.getvalue()


def _build_inactive_route_static_feed() -> bytes:
    from io import BytesIO
    from zipfile import ZipFile

    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr(
            "routes.txt",
            "route_id,route_short_name,route_long_name,route_type\n"
            "455,455,Salem Depot - Wonderland,3\n",
        )
        archive.writestr(
            "trips.txt",
            "route_id,service_id,trip_id,trip_headsign,direction_id\n"
            "455,DAILY,route-455-1,Wonderland,0\n",
        )
        archive.writestr(
            "stop_times.txt",
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "route-455-1,06:00:00,06:00:00,stop-a,1\n"
            "route-455-1,06:30:00,06:30:00,stop-b,2\n",
        )
        archive.writestr(
            "calendar.txt",
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
            "DAILY,1,1,1,1,1,1,1,20240101,20241231\n",
        )
    return payload.getvalue()


def _build_future_trip_filter_static_feed() -> bytes:
    from io import BytesIO
    from zipfile import ZipFile

    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr(
            "routes.txt",
            "route_id,route_short_name,route_long_name,route_type\n"
            "66,66,Harvard Square - Nubian Station,3\n",
        )
        archive.writestr(
            "trips.txt",
            "route_id,service_id,trip_id,trip_headsign,direction_id\n"
            "66,DAILY,bus-now,Nubian,0\n"
            "66,DAILY,bus-future,Nubian,0\n",
        )
        archive.writestr(
            "stop_times.txt",
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "bus-now,08:00:00,08:00:00,stop-a,1\n"
            "bus-now,08:10:00,08:10:00,stop-b,2\n"
            "bus-future,12:00:00,12:00:00,stop-a,1\n"
            "bus-future,12:10:00,12:10:00,stop-b,2\n",
        )
        archive.writestr(
            "calendar.txt",
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
            "DAILY,1,1,1,1,1,1,1,20240101,20241231\n",
        )
    return payload.getvalue()


def _build_trip_chain_dedupe_static_feed() -> bytes:
    from io import BytesIO
    from zipfile import ZipFile

    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr(
            "routes.txt",
            "route_id,route_short_name,route_long_name,route_type\n"
            "66,66,Harvard Square - Nubian Station,3\n",
        )
        archive.writestr(
            "trips.txt",
            "route_id,service_id,trip_id,trip_headsign,direction_id,block_id\n"
            "66,DAILY,bus-chain-1,Nubian,0,C66-1\n"
            "66,DAILY,bus-chain-2,Nubian,0,C66-1\n"
            "66,DAILY,bus-chain-3,Nubian,0,C66-1\n"
            "66,DAILY,bus-chain-4,Nubian,0,C66-1\n",
        )
        archive.writestr(
            "stop_times.txt",
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "bus-chain-1,07:50:00,07:50:00,stop-a,1\n"
            "bus-chain-1,08:05:00,08:05:00,stop-b,2\n"
            "bus-chain-2,08:00:00,08:00:00,stop-a,1\n"
            "bus-chain-2,08:15:00,08:15:00,stop-b,2\n"
            "bus-chain-3,08:10:00,08:10:00,stop-a,1\n"
            "bus-chain-3,08:25:00,08:25:00,stop-b,2\n"
            "bus-chain-4,08:20:00,08:20:00,stop-a,1\n"
            "bus-chain-4,08:35:00,08:35:00,stop-b,2\n",
        )
        archive.writestr(
            "calendar.txt",
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
            "DAILY,1,1,1,1,1,1,1,20240101,20241231\n",
        )
    return payload.getvalue()


def _vehicle_positions_payload() -> str:
    return """{
  "header": {"timestamp": 1710000100},
  "entity": [
    {
      "id": "veh-1811",
      "vehicle": {
        "trip": {"route_id": "Red", "trip_id": "red-1", "direction_id": 0},
        "vehicle": {"id": "1811", "label": "1811"},
        "position": {"latitude": 42.396, "longitude": -71.122},
        "stop_id": "place-davis",
        "current_status": "IN_TRANSIT_TO",
        "current_stop_sequence": 12,
        "timestamp": 1710000100
      }
    },
    {
      "id": "veh-1812",
      "vehicle": {
        "trip": {"route_id": "Red", "trip_id": "red-2", "direction_id": 0},
        "vehicle": {"id": "1812", "label": "1812"},
        "position": {"latitude": 42.397, "longitude": -71.121},
        "stop_id": "place-davis",
        "current_status": "IN_TRANSIT_TO",
        "current_stop_sequence": 13,
        "timestamp": 1710000100
      }
    },
    {
      "id": "veh-1813",
      "vehicle": {
        "trip": {"route_id": "Red", "trip_id": "red-3", "direction_id": 0},
        "vehicle": {"id": "1813", "label": "1813"},
        "position": {"latitude": 42.398, "longitude": -71.12},
        "stop_id": "place-port",
        "current_status": "IN_TRANSIT_TO",
        "current_stop_sequence": 17,
        "timestamp": 1710000100
      }
    }
  ]
}"""


def _sparse_bus_vehicle_positions_payload() -> str:
    return """{
  "header": {"timestamp": 1710000100},
  "entity": [
    {
      "id": "veh-bus-1",
      "vehicle": {
        "trip": {"route_id": "66", "trip_id": "bus-1", "direction_id": 0},
        "vehicle": {"id": "6601", "label": "6601"},
        "position": {"latitude": 42.37, "longitude": -71.11},
        "stop_id": "stop-b",
        "current_status": "IN_TRANSIT_TO",
        "current_stop_sequence": 2,
        "timestamp": 1710000100
      }
    }
  ]
}"""


def _single_bus_vehicle_positions_payload() -> str:
    return """{
  "header": {"timestamp": 1710000100},
  "entity": [
    {
      "id": "veh-bus-1",
      "vehicle": {
        "trip": {"route_id": "66", "trip_id": "bus-1", "direction_id": 0},
        "vehicle": {"id": "6601", "label": "6601"},
        "position": {"latitude": 42.37, "longitude": -71.11},
        "stop_id": "stop-b",
        "current_status": "IN_TRANSIT_TO",
        "current_stop_sequence": 2,
        "timestamp": 1710000100
      }
    }
  ]
}"""


def _trip_updates_payload() -> str:
    return """{
  "header": {"timestamp": 1710000100},
  "entity": [
    {
      "id": "trip-1",
      "trip_update": {
        "trip": {"route_id": "Red", "trip_id": "red-1", "direction_id": 0},
        "vehicle": {"id": "1811"},
        "stop_time_update": [
          {"stop_id": "place-davis", "stop_sequence": 12, "arrival": {"delay": 180}, "departure": {"delay": 240}}
        ]
      }
    },
    {
      "id": "trip-2",
      "trip_update": {
        "trip": {"route_id": "Red", "trip_id": "red-2", "direction_id": 0},
        "vehicle": {"id": "1812"},
        "stop_time_update": [
          {"stop_id": "place-davis", "stop_sequence": 13, "arrival": {"delay": 210}, "departure": {"delay": 270}}
        ]
      }
    },
    {
      "id": "trip-3",
      "trip_update": {
        "trip": {"route_id": "Red", "trip_id": "red-3", "direction_id": 0},
        "vehicle": {"id": "1813"},
        "stop_time_update": [
          {"stop_id": "place-port", "stop_sequence": 17, "arrival": {"delay": 60}, "departure": {"delay": 90}}
        ]
      }
    }
  ]
}"""


def _sparse_bus_trip_updates_payload() -> str:
    return """{
  "header": {"timestamp": 1710000100},
  "entity": [
    {
      "id": "bus-trip-1",
      "trip_update": {
        "trip": {"route_id": "66", "trip_id": "bus-1", "direction_id": 0},
        "vehicle": {"id": "6601"},
        "stop_time_update": [
          {"stop_id": "stop-b", "stop_sequence": 2, "arrival": {"delay": 600}, "departure": {"delay": 660}}
        ]
      }
    },
    {
      "id": "bus-trip-2",
      "trip_update": {
        "trip": {"route_id": "66", "trip_id": "bus-2", "direction_id": 0},
        "stop_time_update": [
          {"stop_id": "stop-b", "stop_sequence": 2, "arrival": {"delay": 540}, "departure": {"delay": 600}}
        ]
      }
    }
  ]
}"""


def _single_bus_trip_updates_payload() -> str:
    return """{
  "header": {"timestamp": 1710000100},
  "entity": [
    {
      "id": "bus-trip-1",
      "trip_update": {
        "trip": {"route_id": "66", "trip_id": "bus-1", "direction_id": 0},
        "vehicle": {"id": "6601"},
        "stop_time_update": [
          {"stop_id": "stop-b", "stop_sequence": 2, "arrival": {"delay": 540}, "departure": {"delay": 600}}
        ]
      }
    }
  ]
}"""


def _future_trip_filter_trip_updates_payload() -> str:
    return """{
  "header": {"timestamp": 1709989860},
  "entity": [
    {
      "id": "bus-now",
      "trip_update": {
        "trip": {"route_id": "66", "trip_id": "bus-now", "direction_id": 0, "start_date": "20240309"},
        "stop_time_update": [
          {"stop_id": "stop-b", "stop_sequence": 2, "arrival": {"delay": 60}, "departure": {"delay": 60}}
        ]
      }
    },
    {
      "id": "bus-future",
      "trip_update": {
        "trip": {"route_id": "66", "trip_id": "bus-future", "direction_id": 0, "start_date": "20240309"},
        "stop_time_update": [
          {"stop_id": "stop-b", "stop_sequence": 2, "arrival": {"delay": 1800}, "departure": {"delay": 1800}}
        ]
      }
    }
  ]
}"""


def _trip_chain_dedupe_trip_updates_payload() -> str:
    return """{
  "header": {"timestamp": 1709989860},
  "entity": [
    {
      "id": "bus-chain-1",
      "trip_update": {
        "trip": {"route_id": "66", "trip_id": "bus-chain-1", "direction_id": 0, "start_date": "20240309"},
        "vehicle": {"id": "6601"},
        "stop_time_update": [
          {"stop_id": "stop-b", "stop_sequence": 2, "arrival": {"delay": 600}, "departure": {"delay": 600}}
        ]
      }
    },
    {
      "id": "bus-chain-2",
      "trip_update": {
        "trip": {"route_id": "66", "trip_id": "bus-chain-2", "direction_id": 0, "start_date": "20240309"},
        "vehicle": {"id": "6601"},
        "stop_time_update": [
          {"stop_id": "stop-b", "stop_sequence": 2, "arrival": {"delay": 600}, "departure": {"delay": 600}}
        ]
      }
    },
    {
      "id": "bus-chain-3",
      "trip_update": {
        "trip": {"route_id": "66", "trip_id": "bus-chain-3", "direction_id": 0, "start_date": "20240309"},
        "vehicle": {"id": "6601"},
        "stop_time_update": [
          {"stop_id": "stop-b", "stop_sequence": 2, "arrival": {"delay": 600}, "departure": {"delay": 600}}
        ]
      }
    },
    {
      "id": "bus-chain-4",
      "trip_update": {
        "trip": {"route_id": "66", "trip_id": "bus-chain-4", "direction_id": 0, "start_date": "20240309"},
        "vehicle": {"id": "6601"},
        "stop_time_update": [
          {"stop_id": "stop-b", "stop_sequence": 2, "arrival": {"delay": 600}, "departure": {"delay": 600}}
        ]
      }
    }
  ]
}"""


def _alerts_payload() -> str:
    return """{
  "header": {"timestamp": 1710000100},
  "entity": [
    {
      "id": "alert-1",
      "alert": {
        "effect": "DETOUR",
        "cause": "TRACK_WORK",
        "header_text": {"translation": [{"text": "Minor Red Line work"}]},
        "informed_entity": [{"route_id": "Red"}]
      }
    }
  ]
}"""


def _facility_alert_payload(route_id: str = "66", timestamp: int = 1710000100) -> str:
    return """{
  "header": {"timestamp": %d},
  "entity": [
    {
      "id": "alert-facility",
      "alert": {
        "effect": "ACCESSIBILITY_ISSUE",
        "cause": "MAINTENANCE",
        "header_text": {"translation": [{"text": "Wonderland Elevator 703 is unavailable due to maintenance."}]},
        "description_text": {"translation": [{"text": "Please use nearby elevator 701 or 702."}]},
        "informed_entity": [{"route_id": "%s"}]
      }
    }
  ]
}""" % (timestamp, route_id)


def _service_alert_payload() -> str:
    return """{
  "header": {"timestamp": 1710000100},
  "entity": [
    {
      "id": "alert-service",
      "alert": {
        "effect": "DETOUR",
        "cause": "CONSTRUCTION",
        "header_text": {"translation": [{"text": "Route 66 is detoured and riders should expect delays."}]},
        "description_text": {"translation": [{"text": "Buses are skipping one stop during this detour."}]},
        "informed_entity": [{"route_id": "66"}]
      }
    }
  ]
}"""


def _inactive_route_alert_payload() -> str:
    return """{
  "header": {"timestamp": 1710021600},
  "entity": [
    {
      "id": "alert-455",
      "alert": {
        "effect": "DETOUR",
        "cause": "CONSTRUCTION",
        "header_text": {"translation": [{"text": "Temporary stop change"}]},
        "informed_entity": [{"route_id": "455"}]
      }
    }
  ]
}"""


def _trip_updates_predicted_time_payload() -> str:
    return """{
  "header": {"timestamp": 1710000100},
  "entity": [
    {
      "id": "trip-1",
      "trip_update": {
        "trip": {"route_id": "Red", "trip_id": "red-1", "direction_id": 0, "start_date": "20240309"},
        "vehicle": {"id": "1811"},
        "stop_time_update": [
          {"stop_id": "place-davis", "stop_sequence": 2, "arrival": {"time": 1709989740}, "departure": {"time": 1709989800}}
        ]
      }
    },
    {
      "id": "trip-2",
      "trip_update": {
        "trip": {"route_id": "Red", "trip_id": "red-2", "direction_id": 0, "start_date": "20240309"},
        "vehicle": {"id": "1812"},
        "stop_time_update": [
          {"stop_id": "place-davis", "stop_sequence": 2, "arrival": {"time": 1709990220}, "departure": {"time": 1709990280}}
        ]
      }
    },
    {
      "id": "trip-3",
      "trip_update": {
        "trip": {"route_id": "Red", "trip_id": "red-3", "direction_id": 0, "start_date": "20240309"},
        "vehicle": {"id": "1813"},
        "stop_time_update": [
          {"stop_id": "place-port", "stop_sequence": 17, "arrival": {"time": 1709990640}, "departure": {"time": 1709990700}}
        ]
      }
    }
  ]
}"""
