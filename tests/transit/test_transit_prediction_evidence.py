from datetime import datetime, timezone

from scripts.transit.prediction_evidence import (
    PREDICTION_EVIDENCE_SCHEMA_VERSION,
    build_prediction_evidence,
)
from scripts.transit.transit_types import (
    GTFSStaticCatalog,
    GTFSStopTime,
    GTFSTrip,
    TransitStopTimeUpdate,
    TransitTripUpdateObservation,
)


def test_prediction_evidence_derives_times_and_retains_skipped_stops():
    catalog = GTFSStaticCatalog(
        feed_label="test",
        trips={"trip-1": GTFSTrip(trip_id="trip-1", route_id="A")},
        stop_times_by_trip={
            "trip-1": [
                GTFSStopTime(
                    trip_id="trip-1",
                    stop_id="stop-1",
                    stop_sequence=1,
                    arrival_time="08:00:00",
                    departure_time="08:01:00",
                ),
                GTFSStopTime(
                    trip_id="trip-1",
                    stop_id="stop-2",
                    stop_sequence=2,
                    arrival_time="08:10:00",
                    departure_time="08:11:00",
                ),
            ]
        },
    )
    update = TransitTripUpdateObservation(
        timestamp_ms=1_710_000_000_000,
        route_id="A",
        trip_id="trip-1",
        direction_id=0,
        service_date="20240309",
        schedule_relationship="SCHEDULED",
        delay_seconds=120,
        stop_time_updates=[
            TransitStopTimeUpdate(
                stop_id="stop-2",
                stop_sequence=2,
                schedule_relationship="SKIPPED",
            ),
            TransitStopTimeUpdate(
                stop_id="stop-1",
                stop_sequence=1,
                arrival_delay_seconds=120,
                departure_time_unix=1_709_971_380,
            ),
        ],
        collection_source="gtfs_rt_trip_updates",
    )

    evidence = build_prediction_evidence(
        catalog,
        [update],
        agency_key="test",
        snapshot_timestamp_ms=1_710_000_010_000,
        feed_timestamp_ms=1_710_000_000_000,
        timezone_name="UTC",
    )

    scheduled_arrival = int(
        datetime(2024, 3, 9, 8, 0, tzinfo=timezone.utc).timestamp()
    )
    assert evidence["schema_version"] == PREDICTION_EVIDENCE_SCHEMA_VERSION
    assert evidence["trip_descriptor_count"] == 1
    assert evidence["trip_descriptors"][0]["schedule_relationship"] == "SCHEDULED"
    assert evidence["event_count"] == 2
    assert evidence["coverage"] == {
        "arrival_time_count": 1,
        "departure_time_count": 1,
        "skipped_stop_count": 1,
    }
    assert [row["stop_sequence"] for row in evidence["events"]] == [1, 2]
    first = evidence["events"][0]
    assert first["trip_schedule_relationship"] == "SCHEDULED"
    assert first["arrival_time_unix"] == scheduled_arrival + 120
    assert first["arrival_time_source"] == "schedule_plus_delay"
    assert first["departure_time_unix"] == 1_709_971_380
    assert first["departure_time_source"] == "gtfs_rt_time"
    skipped = evidence["events"][1]
    assert skipped["schedule_relationship"] == "SKIPPED"
    assert skipped["arrival_time_unix"] is None
    assert skipped["departure_time_unix"] is None


def test_prediction_evidence_explicitly_records_zero_coverage():
    evidence = build_prediction_evidence(
        GTFSStaticCatalog(feed_label="test"),
        [],
        agency_key="test",
        snapshot_timestamp_ms=1_710_000_010_000,
        feed_timestamp_ms=None,
        timezone_name="UTC",
    )

    assert evidence["trip_update_count"] == 0
    assert evidence["event_count"] == 0
    assert evidence["events"] == []
    assert evidence["coverage"]["arrival_time_count"] == 0


def test_prediction_evidence_retains_canceled_trip_without_stop_updates():
    update = TransitTripUpdateObservation(
        timestamp_ms=1_710_000_000_000,
        route_id="A",
        trip_id="trip-canceled",
        direction_id=0,
        service_date="20240309",
        schedule_relationship="CANCELED",
        stop_time_updates=[],
        collection_source="gtfs_rt_trip_updates",
    )

    evidence = build_prediction_evidence(
        GTFSStaticCatalog(feed_label="test"),
        [update],
        agency_key="test",
        snapshot_timestamp_ms=1_710_000_010_000,
        feed_timestamp_ms=1_710_000_000_000,
        timezone_name="UTC",
    )

    assert evidence["trip_update_count"] == 1
    assert evidence["event_count"] == 0
    assert evidence["trip_descriptors"] == [
        {
            "route_id": "A",
            "trip_id": "trip-canceled",
            "direction_id": 0,
            "vehicle_id": None,
            "service_date": "20240309",
            "start_time": None,
            "schedule_relationship": "CANCELED",
            "trip_update_timestamp_ms": 1_710_000_000_000,
            "source": "live",
            "collection_source": "gtfs_rt_trip_updates",
            "trace_id": None,
        }
    ]
