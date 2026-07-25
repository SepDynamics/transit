import json
from pathlib import Path

from scripts.transit.archive import MBTAArchiveConfig, MBTAArchiveService


class _FakeResponse:
    def __init__(self, content: bytes, *, headers=None):
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        return None


class _FakeSession:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def get(self, url, timeout=30):
        self.calls.append((url, timeout))
        return self.mapping[url]


def test_mbta_archive_service_writes_current_and_snapshot_files(tmp_path, monkeypatch):
    monkeypatch.setattr("time.time", lambda: 1_710_000_160)
    session = _FakeSession(
        {
            "https://example.test/gtfs.zip": _FakeResponse(b"PK\x03\x04fake-zip", headers={"ETag": "gtfs-1"}),
            "https://example.test/vehicles.json": _FakeResponse(b'{"entity":[{"id":"v1"}]}', headers={"ETag": "veh-1"}),
            "https://example.test/trips.json": _FakeResponse(b'{"entity":[{"id":"t1"}]}', headers={"ETag": "trip-1"}),
            "https://example.test/alerts.json": _FakeResponse(b'{"entity":[{"id":"a1"}]}', headers={"ETag": "alert-1"}),
        }
    )
    service = MBTAArchiveService(
        MBTAArchiveConfig(
            root_dir=tmp_path,
            interval_seconds=30,
            timeout_seconds=5,
            static_refresh_seconds=3600,
            static_url="https://example.test/gtfs.zip",
            vehicle_positions_url="https://example.test/vehicles.json",
            trip_updates_url="https://example.test/trips.json",
            alerts_url="https://example.test/alerts.json",
        ),
        session=session,
    )

    manifest = service.run_once()

    current_dir = tmp_path / "current"
    snapshot_dir = tmp_path / manifest["snapshot_path"]
    assert (current_dir / "MBTA_GTFS.zip").read_bytes() == b"PK\x03\x04fake-zip"
    assert json.loads((current_dir / "VehiclePositions_enhanced.json").read_text(encoding="utf-8"))["entity"][0]["id"] == "v1"
    assert (snapshot_dir / "Alerts_enhanced.json").exists()
    assert len(manifest["feeds"]) == 4
    assert all(row["status"] == "archived" for row in manifest["feeds"])


def test_mbta_archive_service_skips_static_when_current_copy_is_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr("time.time", lambda: 1_710_000_160)
    current_dir = tmp_path / "current"
    current_dir.mkdir(parents=True, exist_ok=True)
    static_path = current_dir / "MBTA_GTFS.zip"
    static_path.write_bytes(b"existing-static")
    session = _FakeSession(
        {
            "https://example.test/vehicles.json": _FakeResponse(b'{"entity":[]}', headers={"ETag": "veh-1"}),
            "https://example.test/trips.json": _FakeResponse(b'{"entity":[]}', headers={"ETag": "trip-1"}),
            "https://example.test/alerts.json": _FakeResponse(b'{"entity":[]}', headers={"ETag": "alert-1"}),
        }
    )
    service = MBTAArchiveService(
        MBTAArchiveConfig(
            root_dir=tmp_path,
            interval_seconds=30,
            timeout_seconds=5,
            static_refresh_seconds=3600,
            static_url="https://example.test/gtfs.zip",
            vehicle_positions_url="https://example.test/vehicles.json",
            trip_updates_url="https://example.test/trips.json",
            alerts_url="https://example.test/alerts.json",
        ),
        session=session,
    )

    manifest = service.run_once()
    snapshot_dir = tmp_path / manifest["snapshot_path"]

    assert static_path.read_bytes() == b"existing-static"
    static_result = next(row for row in manifest["feeds"] if row["name"] == "static_gtfs")
    assert static_result["status"] == "archived_from_current"
    assert (snapshot_dir / "MBTA_GTFS.zip").read_bytes() == b"existing-static"
    assert all(call[0] != "https://example.test/gtfs.zip" for call in session.calls)


def test_mbta_archive_service_reuses_latest_archived_static_when_current_copy_is_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr("time.time", lambda: 1_710_000_160)
    archived_dir = tmp_path / "archive" / "2024" / "03" / "09" / "010000Z"
    archived_dir.mkdir(parents=True, exist_ok=True)
    (archived_dir / "MBTA_GTFS.zip").write_bytes(b"older-static")
    current_dir = tmp_path / "current"
    current_dir.mkdir(parents=True, exist_ok=True)
    (current_dir / "MBTA_GTFS.zip").write_bytes(b"current-static")
    session = _FakeSession(
        {
            "https://example.test/vehicles.json": _FakeResponse(b'{"entity":[]}', headers={"ETag": "veh-1"}),
            "https://example.test/trips.json": _FakeResponse(b'{"entity":[]}', headers={"ETag": "trip-1"}),
            "https://example.test/alerts.json": _FakeResponse(b'{"entity":[]}', headers={"ETag": "alert-1"}),
        }
    )
    service = MBTAArchiveService(
        MBTAArchiveConfig(
            root_dir=tmp_path,
            interval_seconds=30,
            timeout_seconds=5,
            static_refresh_seconds=3600,
            static_url="https://example.test/gtfs.zip",
            vehicle_positions_url="https://example.test/vehicles.json",
            trip_updates_url="https://example.test/trips.json",
            alerts_url="https://example.test/alerts.json",
        ),
        session=session,
    )

    manifest = service.run_once()
    snapshot_dir = tmp_path / manifest["snapshot_path"]

    static_result = next(row for row in manifest["feeds"] if row["name"] == "static_gtfs")
    assert static_result["status"] == "reused_archived_static"
    assert static_result["path"] == "archive/2024/03/09/010000Z/MBTA_GTFS.zip"
    assert not (snapshot_dir / "MBTA_GTFS.zip").exists()
    assert all(call[0] != "https://example.test/gtfs.zip" for call in session.calls)


def test_mbta_archive_service_can_refresh_current_without_snapshot_archive(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("time.time", lambda: 1_710_000_160)
    session = _FakeSession(
        {
            "https://example.test/gtfs.zip": _FakeResponse(b"PK\x03\x04fake-zip"),
            "https://example.test/vehicles.json": _FakeResponse(
                b'{"entity":[{"id":"v1"}]}'
            ),
            "https://example.test/trips.json": _FakeResponse(
                b'{"entity":[{"id":"t1"}]}'
            ),
            "https://example.test/alerts.json": _FakeResponse(
                b'{"entity":[{"id":"a1"}]}'
            ),
        }
    )
    service = MBTAArchiveService(
        MBTAArchiveConfig(
            root_dir=tmp_path,
            interval_seconds=30,
            timeout_seconds=5,
            static_refresh_seconds=3600,
            static_url="https://example.test/gtfs.zip",
            vehicle_positions_url="https://example.test/vehicles.json",
            trip_updates_url="https://example.test/trips.json",
            alerts_url="https://example.test/alerts.json",
            write_history=False,
        ),
        session=session,
    )

    manifest = service.run_once()

    current_dir = tmp_path / "current"
    assert manifest["snapshot_path"] == "current"
    assert manifest["history_enabled"] is False
    assert (current_dir / "VehiclePositions_enhanced.json").exists()
    assert not (tmp_path / "archive").exists()
    assert all(row["status"] == "current" for row in manifest["feeds"])


def test_archive_rejects_bad_realtime_response_without_replacing_previous_state(tmp_path, monkeypatch):
    monkeypatch.setattr("time.time", lambda: 1_710_000_160)
    current_dir = tmp_path / "current"
    current_dir.mkdir(parents=True)
    previous = b'{"header":{"timestamp":1710000000},"entity":[]}'
    (current_dir / "Alerts_enhanced.json").write_bytes(previous)
    (current_dir / "Alerts_enhanced.json.meta.json").write_text(
        json.dumps({"sha256": "previous-good"}), encoding="utf-8"
    )
    session = _FakeSession(
        {
            "https://example.test/gtfs.zip": _FakeResponse(b"PK\x03\x04fake-zip"),
            "https://example.test/vehicles.json": _FakeResponse(b'{"entity":[]}'),
            "https://example.test/trips.json": _FakeResponse(b'{"entity":[]}'),
            "https://example.test/alerts.json": _FakeResponse(b"<html>gateway error</html>"),
        }
    )
    service = MBTAArchiveService(
        MBTAArchiveConfig(
            root_dir=tmp_path,
            static_url="https://example.test/gtfs.zip",
            vehicle_positions_url="https://example.test/vehicles.json",
            trip_updates_url="https://example.test/trips.json",
            alerts_url="https://example.test/alerts.json",
        ),
        session=session,
    )

    manifest = service.run_once()

    alerts = next(row for row in manifest["feeds"] if row["name"] == "alerts")
    assert alerts["status"] == "degraded_preserved"
    assert alerts["previous_sha256"] == "previous-good"
    assert (current_dir / "Alerts_enhanced.json").read_bytes() == previous
