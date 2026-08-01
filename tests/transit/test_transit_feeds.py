import gzip
from io import BytesIO
from zipfile import ZipFile

import pytest

from scripts.transit.feeds import (
    load_gtfs_catalog,
    merge_realtime_bundles,
    normalize_gtfs_realtime_payload,
    parse_gtfs_realtime_payload,
)


def test_load_gtfs_catalog_builds_route_and_schedule_indexes():
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
            "Red,WKD,red-2,Alewife,0\n",
        )
        archive.writestr(
            "stops.txt",
            "stop_id,stop_name,stop_lat,stop_lon\n"
            "place-alfcl,Alewife,42.395,-71.142\n"
            "place-davis,Davis,42.396,-71.122\n",
        )
        archive.writestr(
            "stop_times.txt",
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "red-1,08:00:00,08:00:00,place-alfcl,1\n"
            "red-1,08:04:00,08:04:30,place-davis,2\n"
            "red-2,08:10:00,08:10:00,place-alfcl,1\n"
            "red-2,08:14:00,08:14:30,place-davis,2\n",
        )
        archive.writestr(
            "transfers.txt",
            "from_stop_id,to_stop_id,transfer_type,min_transfer_time\n"
            "place-davis,place-porter,2,240\n"
            ",place-invalid,0,\n",
        )

    catalog = load_gtfs_catalog(payload.getvalue(), feed_label="mbta")

    assert catalog.feed_label == "mbta"
    assert catalog.route_label("Red") == "Red Red Line"
    assert catalog.trip_label("red-1") == "Red Red Line to Alewife"
    assert catalog.scheduled_headway_seconds("Red", 0) == 600
    assert [row.stop_id for row in catalog.route_stop_times("Red", 0)[:2]] == ["place-alfcl", "place-davis"]
    assert len(catalog.transfers) == 1
    assert catalog.transfers[0].from_stop_id == "place-davis"
    assert catalog.transfers[0].to_stop_id == "place-porter"
    assert catalog.transfers[0].transfer_type == 2
    assert catalog.transfers[0].min_transfer_time == 240


def test_load_gtfs_catalog_can_skip_heavy_static_indexes():
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
            "Red,WKD,red-1,Alewife,0,red-shape\n",
        )
        archive.writestr(
            "stops.txt",
            "stop_id,stop_name,stop_lat,stop_lon\n"
            "place-alfcl,Alewife,42.395,-71.142\n",
        )
        archive.writestr(
            "stop_times.txt",
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "red-1,08:00:00,08:00:00,place-alfcl,1\n",
        )
        archive.writestr(
            "calendar.txt",
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
            "WKD,1,1,1,1,1,0,0,20240101,20250101\n",
        )
        archive.writestr(
            "shapes.txt",
            "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
            "red-shape,42.395,-71.142,1\n",
        )

    catalog = load_gtfs_catalog(
        payload.getvalue(),
        feed_label="mbta",
        include_stops=False,
        include_stop_times=False,
        include_transfers=False,
        include_calendar=False,
        include_shapes=False,
    )

    assert catalog.route_label("Red") == "Red Red Line"
    assert catalog.trip_route_id("red-1") == "Red"
    assert catalog.stops == {}
    assert catalog.stop_times_by_trip == {}
    assert catalog.transfers == []
    assert catalog.calendar == {}
    assert catalog.shapes == {}


def test_normalize_gtfs_rt_payloads_extracts_vehicles_trip_updates_and_alerts():
    vehicle_bundle = normalize_gtfs_realtime_payload(
        {
            "header": {"timestamp": 1_710_000_100},
            "entity": [
                {
                    "id": "vehicle-1",
                    "vehicle": {
                        "trip": {"route_id": "Red", "trip_id": "red-1", "direction_id": 0},
                        "vehicle": {"id": "1811", "label": "1811"},
                        "position": {"latitude": 42.396, "longitude": -71.122, "bearing": 180.0},
                        "stop_id": "place-davis",
                        "current_status": "IN_TRANSIT_TO",
                        "current_stop_sequence": 12,
                        "timestamp": 1_710_000_100,
                    },
                }
            ],
        },
        feed_label="vehicle_positions",
        collection_source="gtfs_rt_vehicle_positions",
        payload_type="vehicle_positions",
    )
    trip_bundle = normalize_gtfs_realtime_payload(
        {
            "header": {"timestamp": 1_710_000_100},
            "entity": [
                {
                    "id": "trip-1",
                    "trip_update": {
                        "trip": {"route_id": "Red", "trip_id": "red-1", "direction_id": 0},
                        "vehicle": {"id": "1811"},
                        "stop_time_update": [
                            {
                                "stop_id": "place-davis",
                                "stop_sequence": 12,
                                "arrival": {"delay": 180},
                                "departure": {"delay": 240},
                            }
                        ],
                    },
                }
            ],
        },
        feed_label="trip_updates",
        collection_source="gtfs_rt_trip_updates",
        payload_type="trip_updates",
    )
    alert_bundle = normalize_gtfs_realtime_payload(
        {
            "header": {"timestamp": 1_710_000_100},
            "entity": [
                {
                    "id": "alert-1",
                    "alert": {
                        "effect": "SIGNIFICANT_DELAYS",
                        "cause": "CONGESTION",
                        "active_period": [
                            {"start": 1_710_000_000, "end": 1_710_000_200}
                        ],
                        "header_text": {"translation": [{"text": "Red Line delays"}]},
                        "informed_entity": [{"route_id": "Red"}],
                    },
                }
            ],
        },
        feed_label="alerts",
        collection_source="gtfs_rt_alerts",
        payload_type="alerts",
    )

    merged = merge_realtime_bundles("Transit Sentinel", vehicle_bundle, trip_bundle, alert_bundle)

    assert merged.feed_timestamp_ms == 1_710_000_100_000
    assert merged.vehicles[0].vehicle_id == "1811"
    assert merged.vehicles[0].service_date is None
    assert merged.trip_updates[0].delay_seconds == 240
    assert merged.trip_updates[0].schedule_relationship is None
    assert merged.trip_updates[0].stop_time_updates[0].arrival_time_unix is None
    assert merged.alerts[0].route_ids == ["Red"]
    assert merged.alerts[0].header_text == "Red Line delays"
    assert merged.alerts[0].active_periods == [
        {"start_ms": 1_710_000_000_000, "end_ms": 1_710_000_200_000}
    ]
    assert merged.alerts[0].is_active_at(1_710_000_100_000) is True
    assert merged.alerts[0].is_active_at(1_710_000_300_000) is False


def test_gtfs_rt_parser_accepts_gzip_json_and_rejects_empty_payloads():
    raw = gzip.compress(b'{"header":{"timestamp":1710000100},"entity":[]}')

    assert parse_gtfs_realtime_payload(raw, content_encoding="gzip")["entity"] == []
    with pytest.raises(ValueError, match="empty"):
        parse_gtfs_realtime_payload(b"")


@pytest.mark.parametrize(
    ("raw_relationship", "expected_relationship"),
    [
        ("CANCELED", "CANCELED"),
        (7, "DELETED"),
        ("replacement", "REPLACEMENT"),
        (6, "DUPLICATED"),
        (8, "NEW"),
    ],
)
def test_trip_descriptor_schedule_relationship_is_retained_and_normalized(
    raw_relationship, expected_relationship
):
    bundle = normalize_gtfs_realtime_payload(
        {
            "header": {"timestamp": 1_710_000_100},
            "entity": [
                {
                    "id": "trip-1",
                    "trip_update": {
                        "trip": {
                            "route_id": "Red",
                            "trip_id": "red-1",
                            "schedule_relationship": raw_relationship,
                        }
                    },
                }
            ],
        },
        feed_label="trip_updates",
        collection_source="gtfs_rt_trip_updates",
        payload_type="trip_updates",
    )

    assert bundle.trip_updates[0].schedule_relationship == expected_relationship
