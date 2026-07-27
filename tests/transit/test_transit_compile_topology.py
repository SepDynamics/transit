import gzip
import json
import math
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

import scripts.transit.compile_topology as compiler
from scripts.transit.advisory import load_transit_topology
from scripts.transit.compile_topology import (
    TopologyCompileError,
    compile_and_save_gtfs_topology,
    compile_gtfs_topology,
)


ROUTES = """route_id,route_short_name,route_long_name,route_type
R1,1,First Line,3
R2,2,Second Line,3
"""

TRIPS = """route_id,service_id,trip_id,direction_id
R1,WK,t1,0
R1,WK,t0,0
R2,WK,t2,1
R2,WK,t3,1
"""

STOPS = """stop_id,stop_name,stop_lat,stop_lon,parent_station,location_type
HUB,Hub,34.00005,-118.00000,,1
A,Platform A,34.00000,-118.00000,HUB,0
D,Platform D,34.00010,-118.00000,HUB,0
B,Middle,34.01000,-118.00000,,0
C,Near West,34.02000,-118.00000,,0
E,Near East,34.02050,-118.00000,,0
"""

GROUPED_STOP_TIMES = """trip_id,arrival_time,departure_time,stop_id,stop_sequence
t1,08:00:00,08:00:00,A,1
t1,08:05:00,08:06:00,B,2
t1,08:10:00,08:10:00,C,3
t0,09:00:00,09:00:00,A,1
t0,09:04:00,09:05:00,B,2
t0,09:11:00,09:11:00,C,3
t2,10:00:00,10:00:00,D,10
t2,10:05:00,10:05:00,B,20
t3,11:00:00,11:00:00,E,1
"""

TRANSFERS = """from_stop_id,to_stop_id,transfer_type,min_transfer_time
A,C,2,180
C,A,3,999
E,C,2,200
"""


def _write_feed(directory: Path, *, stop_times: str = GROUPED_STOP_TIMES) -> Path:
    directory.mkdir()
    for name, contents in {
        "routes.txt": ROUTES,
        "trips.txt": TRIPS,
        "stops.txt": STOPS,
        "stop_times.txt": stop_times,
        "transfers.txt": TRANSFERS,
    }.items():
        (directory / name).write_text(contents, encoding="utf-8")
    return directory


def _zip_feed(source: Path, destination: Path) -> Path:
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for path in reversed(sorted(source.iterdir())):
            archive.writestr(f"nested/{path.name}", path.read_bytes())
    return destination


def test_streaming_compiler_builds_compact_advisory_artifact(tmp_path: Path) -> None:
    source = _write_feed(tmp_path / "feed")
    output = tmp_path / "topology.json"

    compiled = compile_and_save_gtfs_topology(
        source,
        output,
        feed_label="test-feed",
        max_nearby_walk_meters=100.0,
        walking_speed_mps=2.0,
        station_transfer_floor_seconds=75,
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["metadata"] == compiled.metadata
    assert payload["metadata"]["counts"] == {
        "agency_transfer_records": 2,
        "compiled_trips": 4,
        "inferred_nearby_stop_edges": 1,
        "inferred_shared_station_edges": 2,
        "inferred_shared_stop_edges": 1,
        "patterns": 3,
        "prohibited_transfer_records": 1,
        "ride_edges": 3,
        "routes": 2,
        "served_stops": 5,
        "source_transfers": 3,
        "source_trips": 4,
        "stop_time_rows": 9,
        "stops": 6,
        "transfer_edges": 6,
        "trips_without_stop_times": 0,
    }
    assert payload["routes"] == [
        {"label": "1 First Line", "route_id": "R1", "route_type": 3},
        {"label": "2 Second Line", "route_id": "R2", "route_type": 3},
    ]
    assert len(payload["patterns"]) == 3
    assert payload["trips"] == [
        {"pattern_id": "pattern-000001", "trip_id": "t0"},
        {"pattern_id": "pattern-000001", "trip_id": "t1"},
        {"pattern_id": "pattern-000002", "trip_id": "t2"},
        {"pattern_id": "pattern-000003", "trip_id": "t3"},
    ]

    topology = load_transit_topology(output)
    assert topology.trip_paths["t0"].stops is topology.trip_paths["t1"].stops
    edges = {
        (edge.route_id, edge.direction_id, edge.from_stop_id, edge.to_stop_id): edge
        for edge in topology.ride_edges
    }
    assert edges[("R1", 0, "A", "B")].trip_id == "t0"
    assert edges[("R1", 0, "A", "B")].scheduled_travel_seconds == 270
    assert edges[("R1", 0, "B", "C")].scheduled_travel_seconds == 300

    transfers = {
        (edge.from_stop_id, edge.to_stop_id): edge for edge in topology.transfer_edges
    }
    assert transfers[("A", "C")].source == "agency_defined"
    assert transfers[("A", "C")].minimum_transfer_seconds == math.ceil(
        (transfers[("A", "C")].distance_meters or 0) / 2.0
    )
    assert transfers[("A", "C")].minimum_transfer_seconds > 180
    assert ("C", "A") not in transfers
    assert transfers[("A", "D")].source == "inferred_shared_station"
    assert transfers[("A", "D")].minimum_transfer_seconds == 75
    assert transfers[("C", "E")].source == "inferred_nearby_stop"
    assert transfers[("C", "E")].minimum_transfer_seconds == 28
    assert transfers[("E", "C")].source == "agency_defined"
    assert transfers[("E", "C")].minimum_transfer_seconds == 200
    assert transfers[("B", "B")].source == "inferred_shared_stop"


def test_zip_and_directory_produce_deterministic_json_and_gzip(tmp_path: Path) -> None:
    source = _write_feed(tmp_path / "feed")
    archive = _zip_feed(source, tmp_path / "feed.zip")
    directory_output = tmp_path / "directory.json"
    zip_output = tmp_path / "zip.json"
    gzip_one = tmp_path / "one.json.gz"
    gzip_two = tmp_path / "two.json.gz"
    options = {
        "feed_label": "stable-feed",
        "max_nearby_walk_meters": 100.0,
        "walking_speed_mps": 2.0,
        "station_transfer_floor_seconds": 75,
    }

    compile_and_save_gtfs_topology(source, directory_output, **options)
    compile_and_save_gtfs_topology(archive, zip_output, **options)
    compile_and_save_gtfs_topology(archive, gzip_one, **options)
    compile_and_save_gtfs_topology(archive, gzip_two, **options)

    assert directory_output.read_bytes() == zip_output.read_bytes()
    assert gzip_one.read_bytes() == gzip_two.read_bytes()
    assert gzip_one.read_bytes().startswith(b"\x1f\x8b")
    assert json.loads(gzip.decompress(gzip_one.read_bytes())) == json.loads(
        directory_output.read_text(encoding="utf-8")
    )
    assert load_transit_topology(gzip_one).feed_label == "stable-feed"


def test_streaming_transfer_tombstones_and_safe_recommended_durations(tmp_path: Path) -> None:
    source = _write_feed(tmp_path / "feed")
    (source / "stops.txt").write_text(
        STOPS
        + """N1,No coordinates 1,,,,0
N2,No coordinates 2,,,,0
""",
        encoding="utf-8",
    )
    (source / "transfers.txt").write_text(
        """from_stop_id,to_stop_id,transfer_type,min_transfer_time
A,D,3,
C,E,0,
N1,N2,1,
B,B,0,
""",
        encoding="utf-8",
    )

    compiled = compile_gtfs_topology(
        source,
        max_nearby_walk_meters=100,
        walking_speed_mps=2.0,
        station_transfer_floor_seconds=75,
    )
    transfers = {
        (edge.from_stop_id, edge.to_stop_id): edge
        for edge in compiled.topology.transfer_edges
    }

    assert ("A", "D") not in transfers
    assert transfers[("D", "A")].source == "inferred_shared_station"
    assert transfers[("C", "E")].source == "agency_defined"
    assert transfers[("C", "E")].minimum_transfer_seconds == 28
    assert transfers[("E", "C")].source == "inferred_nearby_stop"
    assert transfers[("E", "C")].minimum_transfer_seconds == 28
    assert transfers[("N1", "N2")].minimum_transfer_seconds == 75
    assert transfers[("B", "B")].minimum_transfer_seconds == 0
    assert compiled.metadata["counts"]["prohibited_transfer_records"] == 1


@pytest.mark.parametrize(
    ("stop_times", "message"),
    [
        (
            """trip_id,arrival_time,departure_time,stop_id,stop_sequence
t1,08:00:00,08:00:00,A,1
t2,09:00:00,09:00:00,D,1
t1,08:05:00,08:05:00,B,2
""",
            "appears in multiple groups",
        ),
        (
            """trip_id,arrival_time,departure_time,stop_id,stop_sequence
t1,08:00:00,08:00:00,A,2
t1,08:05:00,08:05:00,B,1
""",
            "non-increasing stop_sequence",
        ),
    ],
)
def test_compiler_rejects_ungrouped_or_unordered_stop_times(
    tmp_path: Path, stop_times: str, message: str
) -> None:
    source = _write_feed(tmp_path / "feed", stop_times=stop_times)

    with pytest.raises(TopologyCompileError, match=message):
        compile_gtfs_topology(source)


def test_failed_compile_does_not_replace_existing_artifact(tmp_path: Path) -> None:
    stop_times = """trip_id,arrival_time,departure_time,stop_id,stop_sequence
t1,08:00:00,08:00:00,A,2
t1,08:05:00,08:05:00,B,1
"""
    source = _write_feed(tmp_path / "feed", stop_times=stop_times)
    output = tmp_path / "topology.json"
    output.write_bytes(b"existing-artifact\n")

    with pytest.raises(TopologyCompileError):
        compile_and_save_gtfs_topology(source, output)

    assert output.read_bytes() == b"existing-artifact\n"


def test_atomic_writer_cleans_partial_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _write_feed(tmp_path / "feed")
    compiled = compile_gtfs_topology(source, max_nearby_walk_meters=0)
    output = tmp_path / "topology.json"
    output.write_bytes(b"existing-artifact\n")

    def fail_save(_topology: object, destination: Path, **_kwargs: object) -> None:
        destination.write_bytes(b"partial")
        raise OSError("simulated write failure")

    monkeypatch.setattr(compiler, "save_transit_topology", fail_save)
    with pytest.raises(OSError, match="simulated write failure"):
        compiler._atomic_save(compiled, output)

    assert output.read_bytes() == b"existing-artifact\n"
    assert list(tmp_path.glob(".topology.json.*")) == []
