import json

from scripts.transit.notify import NotificationDispatcher, NotifyConfig
from scripts.transit.proof_windows import (
    TransitProofWindowConfig,
    capture_proof_window,
)


def test_capture_proof_window_copies_archive_window(tmp_path):
    archive_root = tmp_path / "feeds" / "mbta"
    center_timestamp_ms = 1_775_563_800_000
    _write_snapshot(
        archive_root / "archive" / "2026" / "04" / "07" / "113000Z",
        timestamp_ms=center_timestamp_ms - (30 * 60 * 1000),
    )
    _write_snapshot(
        archive_root / "archive" / "2026" / "04" / "07" / "121000Z",
        timestamp_ms=center_timestamp_ms + (10 * 60 * 1000),
    )
    _write_snapshot(
        archive_root / "archive" / "2026" / "04" / "07" / "133500Z",
        timestamp_ms=center_timestamp_ms + (95 * 60 * 1000),
    )

    manifest = capture_proof_window(
        TransitProofWindowConfig(
            archive_root=archive_root,
            output_root=tmp_path / "artifacts" / "proof-windows",
            incident={
                "incident_id": "route:Red:0:hold:bunching_onset",
                "agency_key": "mbta",
                "timestamp_ms": center_timestamp_ms,
            },
            before_minutes=60,
            after_minutes=60,
        )
    )

    bundle_root = tmp_path / "artifacts" / "proof-windows" / "mbta" / manifest["bundle_id"]

    assert manifest["snapshot_count"] == 2
    assert (bundle_root / "proof_window.json").exists()
    assert (
        bundle_root / "archive" / "2026" / "04" / "07" / "113000Z" / "manifest.json"
    ).exists()
    assert not (
        bundle_root / "archive" / "2026" / "04" / "07" / "133500Z" / "manifest.json"
    ).exists()


def test_notification_dispatcher_captures_proof_window_when_configured(tmp_path):
    archive_root = tmp_path / "feeds" / "mbta"
    incident_timestamp_ms = 1_775_563_800_000
    _write_snapshot(
        archive_root / "archive" / "2026" / "04" / "07" / "120000Z",
        timestamp_ms=incident_timestamp_ms,
    )

    dispatcher = NotificationDispatcher(
        NotifyConfig(
            proof_archive_root=str(archive_root),
            proof_output_root=str(tmp_path / "artifacts" / "proof-windows"),
            proof_before_minutes=30,
            proof_after_minutes=30,
        )
    )
    dispatcher.dispatch(
        {
            "incident_id": "route:Red:0:dispatch_relief:headway_collapse",
            "entity_id": "route:Red:0",
            "agency_key": "mbta",
            "severity": "warning",
            "timestamp_ms": incident_timestamp_ms,
        },
        agency="MBTA",
        agency_key="mbta",
    )
    dispatcher.close()

    captured = list((tmp_path / "artifacts" / "proof-windows" / "mbta").glob("*"))
    assert len(captured) == 1
    proof_manifest = json.loads((captured[0] / "proof_window.json").read_text())
    assert proof_manifest["snapshot_count"] == 1
    assert proof_manifest["incident"]["incident_id"].endswith("headway_collapse")


def _write_snapshot(snapshot_dir, *, timestamp_ms: int) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
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
    (snapshot_dir / "VehiclePositions_enhanced.json").write_text(
        '{"header":{"timestamp":1710000100},"entity":[]}', encoding="utf-8"
    )
