import hashlib
import json
import tarfile
from datetime import datetime, timezone

from scripts.transit.archive_bundle import bundle_hour
from scripts.transit.candidate_index import build_index
from scripts.transit.pull_lametro import verify_bundle


def _write_snapshot(root, stamp, *, failed_rail=True):
    snapshot = root / "archive" / stamp.strftime("%Y/%m/%d/%H%M%SZ")
    snapshot.mkdir(parents=True)
    timestamp_ms = int(stamp.timestamp() * 1000)
    manifest = {
        "agency": "LA Metro",
        "agency_key": "lametro",
        "captured_at": stamp.isoformat(),
        "timestamp_ms": timestamp_ms,
        "snapshot_path": str(snapshot.relative_to(root)),
        "mode_status": {"bus": "available", "rail": "unavailable" if failed_rail else "available"},
        "feeds": ([{"name": "rail_vehicle_positions", "status": "failed"}] if failed_rail else []),
    }
    (snapshot / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (snapshot / "bus_alerts.pb").write_text(
        json.dumps(
            {
                "header": {"timestamp": int(stamp.timestamp())},
                "entity": [
                    {
                        "id": "a1",
                        "alert": {"effect": "NO_SERVICE", "header_text": {"translation": [{"text": "No service"}]}},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (snapshot / "bus_vehicle_positions.pb").write_text(
        json.dumps({"header": {"timestamp": int(stamp.timestamp())}, "entity": []}), encoding="utf-8"
    )
    (snapshot / "bus_trip_updates.pb").write_text(
        json.dumps({"header": {"timestamp": int(stamp.timestamp())}, "entity": []}), encoding="utf-8"
    )
    return snapshot


def test_hourly_bundle_is_verified_before_sources_are_removed(tmp_path):
    hour = datetime(2026, 7, 27, 20, tzinfo=timezone.utc)
    snapshot = _write_snapshot(tmp_path, hour.replace(minute=1))

    bundle = bundle_hour(tmp_path, hour, min_free_gb=0)

    assert bundle is not None
    assert not snapshot.exists()
    assert verify_bundle(bundle)
    with tarfile.open(bundle, "r:gz") as archive:
        assert any(name.endswith("/manifest.json") for name in archive.getnames())


def test_hourly_bundle_includes_referenced_static_anchor_once(tmp_path):
    hour = datetime(2026, 7, 27, 20, tzinfo=timezone.utc)
    snapshot = _write_snapshot(tmp_path, hour.replace(minute=1))
    anchor = tmp_path / "anchors" / "bus_gtfs_abc.zip"
    anchor.parent.mkdir()
    anchor.write_bytes(b"PK\x03\x04anchor")
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["feeds"].append({"name": "bus_static_gtfs", "status": "captured", "path": "anchors/bus_gtfs_abc.zip"})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    bundle = bundle_hour(tmp_path, hour, min_free_gb=0)

    with tarfile.open(bundle, "r:gz") as archive:
        assert archive.getnames().count("anchors/bus_gtfs_abc.zip") == 1
    assert anchor.exists()


def test_candidate_index_flags_impacts_and_missing_rail(tmp_path):
    incoming = tmp_path / "incoming"
    hour = datetime(2026, 7, 27, 20, tzinfo=timezone.utc)
    source_root = tmp_path / "source"
    _write_snapshot(source_root, hour.replace(minute=1))
    bundle = bundle_hour(source_root, hour, min_free_gb=0)
    target = incoming / "2026" / "07" / "27" / "20.tar.gz"
    target.parent.mkdir(parents=True)
    target.write_bytes(bundle.read_bytes())
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(".tar.gz.sha256").write_text(f"{digest}  20.tar.gz\n", encoding="utf-8")

    report = build_index(incoming)

    assert report["snapshot_count"] == 1
    assert report["candidate_count"] == 1
    assert report["signal_counts"]["missing_feed"] == 1
    assert report["signal_counts"]["service_impact_alert"] == 1
    assert report["candidates"][0]["control_stratum"] == "weekday_off_peak"
