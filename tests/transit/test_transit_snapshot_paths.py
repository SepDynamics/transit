import json

from scripts.transit.snapshot_paths import resolve_snapshot_feed_paths


def test_resolve_snapshot_feed_paths_maps_lametro_bus_discovery_names(tmp_path):
    snapshot = tmp_path / "archive" / "2026" / "08" / "01" / "120000Z"
    snapshot.mkdir(parents=True)
    manifest = {
        "agency": "LA Metro",
        "agency_key": "lametro",
        "snapshot_path": "archive/2026/08/01/120000Z",
        "feeds": [
            {"name": "bus_static_gtfs", "path": "anchors/bus.zip"},
            {
                "name": "bus_vehicle_positions",
                "path": "archive/2026/08/01/120000Z/bus_vehicle_positions.pb",
            },
            {
                "name": "bus_trip_updates",
                "path": "archive/2026/08/01/120000Z/bus_trip_updates.pb",
            },
            {
                "name": "bus_alerts",
                "path": "archive/2026/08/01/120000Z/bus_alerts.pb",
            },
        ],
    }
    (snapshot / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    paths = resolve_snapshot_feed_paths(snapshot)

    assert paths["static_gtfs"] == str((tmp_path / "anchors" / "bus.zip").resolve())
    assert paths["vehicle_positions"] == str((snapshot / "bus_vehicle_positions.pb").resolve())
    assert paths["trip_updates"] == str((snapshot / "bus_trip_updates.pb").resolve())
    assert paths["alerts"] == str((snapshot / "bus_alerts.pb").resolve())
