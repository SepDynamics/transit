import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from scripts.transit.calibration import (
    build_transit_baseline_incidents,
    build_transit_calibration_report,
    build_transit_calibration_suite_report,
    discover_transit_label_files,
    normalize_transit_label_payload,
    render_transit_calibration_markdown,
    render_transit_calibration_suite_markdown,
)


def test_transit_calibration_report_supports_bunching_use_case(tmp_path):
    snapshot_dir = tmp_path / "archive" / "2026" / "04" / "04" / "010000Z"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "MBTA_GTFS.zip").write_bytes(_build_static_feed())
    (snapshot_dir / "VehiclePositions_enhanced.json").write_text(_vehicle_positions_payload(), encoding="utf-8")
    (snapshot_dir / "TripUpdates_enhanced.json").write_text(_trip_updates_payload(), encoding="utf-8")
    (snapshot_dir / "Alerts_enhanced.json").write_text('{"header":{"timestamp":1710000100},"entity":[]}', encoding="utf-8")
    labels = {
        "dataset_id": "mbta-bunching-proof",
        "use_case": "Detect bunching onset on a corridor before naive alert-or-delay thresholds would escalate it.",
        "incidents": [
            {
                "incident_id": "red-bunching-001",
                "snapshot_path": "archive/2026/04/04/010000Z",
                "route_id": "Red",
                "direction_id": 0,
                "expected_regime": "bunching_onset",
                "expected_action": "hold",
                "use_case": "corridor bunching triage",
            }
        ],
    }

    report = build_transit_calibration_report(tmp_path, labels)
    markdown = render_transit_calibration_markdown(report)

    assert report["sentinel"]["matched_incident_count"] == 1
    assert report["baseline"]["matched_incident_count"] == 0
    assert report["comparison"]["value_case_supported"] is True
    assert "bunching onset" in markdown.lower()


def test_transit_calibration_report_uses_manifest_feed_paths_for_static_gtfs(tmp_path):
    archived_static = tmp_path / "archive" / "2026" / "04" / "04" / "000000Z"
    archived_static.mkdir(parents=True, exist_ok=True)
    (archived_static / "MBTA_GTFS.zip").write_bytes(_build_static_feed())

    snapshot_dir = tmp_path / "archive" / "2026" / "04" / "04" / "010000Z"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "VehiclePositions_enhanced.json").write_text(_vehicle_positions_payload(), encoding="utf-8")
    (snapshot_dir / "TripUpdates_enhanced.json").write_text(_trip_updates_payload(), encoding="utf-8")
    (snapshot_dir / "Alerts_enhanced.json").write_text('{"header":{"timestamp":1710000100},"entity":[]}', encoding="utf-8")
    (snapshot_dir / "manifest.json").write_text(
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
    labels = {
        "dataset_id": "mbta-bunching-proof",
        "incidents": [
            {
                "incident_id": "red-bunching-001",
                "snapshot_path": "archive/2026/04/04/010000Z",
                "route_id": "Red",
                "direction_id": 0,
                "expected_regime": "bunching_onset",
                "expected_action": "hold",
            }
        ],
    }

    report = build_transit_calibration_report(tmp_path, labels)

    assert report["sentinel"]["matched_incident_count"] == 1


def test_normalize_transit_label_payload_requires_snapshot_and_route():
    payload = normalize_transit_label_payload(
        {
            "incidents": [
                {
                    "snapshot_path": "archive/2026/04/04/010000Z",
                    "route_id": "Red",
                    "expected_regime": "bunching_onset",
                    "expected_action": "hold",
                }
            ]
        }
    )

    assert payload["incidents"][0]["route_id"] == "Red"
    assert payload["incidents"][0]["expected_action"] == "hold"


def test_transit_calibration_report_supports_negative_control_labels(tmp_path):
    snapshot_dir = tmp_path / "archive" / "2026" / "04" / "04" / "010000Z"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "MBTA_GTFS.zip").write_bytes(_build_static_feed())
    (snapshot_dir / "VehiclePositions_enhanced.json").write_text(_vehicle_positions_payload(), encoding="utf-8")
    (snapshot_dir / "TripUpdates_enhanced.json").write_text(_trip_updates_payload(), encoding="utf-8")
    (snapshot_dir / "Alerts_enhanced.json").write_text('{"header":{"timestamp":1710000100},"entity":[]}', encoding="utf-8")
    labels = {
        "dataset_id": "mbta-control-proof",
        "incidents": [
            {
                "incident_id": "red-should-not-be-silent",
                "snapshot_path": "archive/2026/04/04/010000Z",
                "route_id": "Red",
                "direction_id": 0,
                "expected_detection": False,
                "note": "This synthetic bunching case should violate a no-incident control label.",
            }
        ],
    }

    report = build_transit_calibration_report(tmp_path, labels)
    markdown = render_transit_calibration_markdown(report)

    assert report["negative_label_count"] == 1
    assert report["sentinel"]["control_violation_count"] == 1
    assert report["sentinel"]["satisfied_label_count"] == 0
    assert report["comparison"]["value_case_supported"] is False
    assert "expected `no incident`" in markdown


def test_discover_transit_label_files_supports_directory_inputs(tmp_path):
    labels_dir = tmp_path / "labels"
    labels_dir.mkdir()
    (labels_dir / "b.json").write_text("{}", encoding="utf-8")
    nested_dir = labels_dir / "nested"
    nested_dir.mkdir()
    (nested_dir / "a.json").write_text("{}", encoding="utf-8")

    files = discover_transit_label_files(labels_dir)

    assert [path.name for path in files] == ["b.json", "a.json"]


def test_transit_calibration_suite_report_aggregates_case_pack_directory(tmp_path):
    snapshot_dir = tmp_path / "archive" / "2026" / "04" / "04" / "010000Z"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "MBTA_GTFS.zip").write_bytes(_build_static_feed())
    (snapshot_dir / "VehiclePositions_enhanced.json").write_text(_vehicle_positions_payload(), encoding="utf-8")
    (snapshot_dir / "TripUpdates_enhanced.json").write_text(_trip_updates_payload(), encoding="utf-8")
    (snapshot_dir / "Alerts_enhanced.json").write_text('{"header":{"timestamp":1710000100},"entity":[]}', encoding="utf-8")
    labels_dir = tmp_path / "labels"
    labels_dir.mkdir()
    for dataset_id in ("mbta-bunching-am", "mbta-bunching-pm"):
        (labels_dir / f"{dataset_id}.json").write_text(
            json.dumps(
                {
                    "dataset_id": dataset_id,
                    "use_case": "Detect bunching onset on a corridor before naive alert-or-delay thresholds would escalate it.",
                    "incidents": [
                        {
                            "incident_id": f"{dataset_id}-001",
                            "snapshot_path": "archive/2026/04/04/010000Z",
                            "route_id": "Red",
                            "direction_id": 0,
                            "expected_regime": "bunching_onset",
                            "expected_action": "hold",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    report = build_transit_calibration_suite_report(tmp_path, labels_dir)
    markdown = render_transit_calibration_suite_markdown(report)

    assert report["case_pack_count"] == 2
    assert report["label_count"] == 2
    assert report["comparison"]["passing_case_pack_count"] == 2
    assert report["comparison"]["value_case_supported"] is True
    assert "mbta-bunching-am" in markdown
    assert "PASS" in markdown


def test_transit_calibration_suite_report_supports_nested_case_pack_root(tmp_path):
    pack_root = tmp_path / "case-packs"
    snapshot_dir = pack_root / "mbta-red-pack" / "archive" / "2026" / "04" / "04" / "010000Z"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (snapshot_dir / "MBTA_GTFS.zip").write_bytes(_build_static_feed())
    (snapshot_dir / "VehiclePositions_enhanced.json").write_text(_vehicle_positions_payload(), encoding="utf-8")
    (snapshot_dir / "TripUpdates_enhanced.json").write_text(_trip_updates_payload(), encoding="utf-8")
    (snapshot_dir / "Alerts_enhanced.json").write_text('{"header":{"timestamp":1710000100},"entity":[]}', encoding="utf-8")
    labels_dir = pack_root / "mbta-red-pack" / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    (labels_dir / "bunching.json").write_text(
        json.dumps(
            {
                "dataset_id": "mbta-nested-red-pack",
                "incidents": [
                    {
                        "incident_id": "nested-red-001",
                        "snapshot_path": "archive/2026/04/04/010000Z",
                        "route_id": "Red",
                        "direction_id": 0,
                        "expected_regime": "bunching_onset",
                        "expected_action": "hold",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_transit_calibration_suite_report(pack_root, pack_root)

    assert report["case_pack_count"] == 1
    assert report["comparison"]["passing_case_pack_count"] == 1
    assert report["sentinel"]["matched_incident_count"] == 1


def test_committed_mbta_overnight_control_case_packs_pass():
    repo_root = Path(__file__).resolve().parents[2]
    archive_root = repo_root / "data" / "case-packs" / "mbta" / "overnight_advisory_controls"
    labels_root = archive_root / "labels"

    report = build_transit_calibration_suite_report(archive_root, labels_root)
    markdown = render_transit_calibration_suite_markdown(report)

    assert report["case_pack_count"] == 3
    assert report["sentinel"]["control_violation_count"] == 0
    assert report["sentinel"]["label_success_rate"] == 1.0
    assert report["comparison"]["value_case_supported"] is True
    assert "mbta-overnight-planned-service-controls" in markdown


def test_committed_mbta_daytime_positive_case_pack_passes():
    repo_root = Path(__file__).resolve().parents[2]
    archive_root = repo_root / "data" / "case-packs" / "mbta" / "daytime_red_line_delay_spike"
    labels_root = archive_root / "labels"

    report = build_transit_calibration_suite_report(archive_root, labels_root)
    markdown = render_transit_calibration_suite_markdown(report)

    assert report["case_pack_count"] == 1
    assert report["sentinel"]["matched_incident_count"] == 1
    assert report["sentinel"]["action_match_count"] == 1
    assert report["comparison"]["value_case_supported"] is True
    assert "mbta-red-line-midday-delay-spike" in markdown


def test_committed_mbta_combined_case_pack_suite_passes():
    repo_root = Path(__file__).resolve().parents[2]
    mbta_case_pack_root = repo_root / "data" / "case-packs" / "mbta"

    report = build_transit_calibration_suite_report(mbta_case_pack_root, mbta_case_pack_root)
    markdown = render_transit_calibration_suite_markdown(report)

    assert report["case_pack_count"] == 4
    assert report["label_count"] == 8
    assert report["sentinel"]["matched_incident_count"] == 1
    assert report["sentinel"]["control_violation_count"] == 0
    assert report["sentinel"]["label_success_rate"] == 1.0
    assert report["comparison"]["passing_case_pack_count"] == 4
    assert report["comparison"]["value_case_supported"] is True
    assert "mbta-overnight-planned-service-controls" in markdown
    assert "mbta-red-line-midday-delay-spike" in markdown


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
