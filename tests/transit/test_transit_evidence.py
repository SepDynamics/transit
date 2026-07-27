import json
from datetime import datetime, timezone

from scripts.transit.evidence import EVIDENCE_SCHEMA_VERSION, EvidenceArchive


def test_evidence_archive_writes_versioned_partitioned_snapshot(tmp_path):
    destination = EvidenceArchive(tmp_path).append_snapshot(
        {
            "health": {"timestamp_ms": 1_710_000_000_000, "status": "ok"},
            "feed_status": {"status": "ok", "vehicle_count": 4},
            "entities": {"vehicles": [{"entity_id": "vehicle:1"}]},
            "regimes": {"regimes": []},
            "incidents": {"incidents": []},
            "prediction_evidence": {
                "schema_version": "sentinel.prediction_evidence.v1",
                "event_count": 1,
                "events": [{"trip_id": "trip-1", "stop_id": "stop-1"}],
            },
        },
        agency_key="lametro",
        archive_manifest={"feeds": [{"name": "alerts", "status": "archived"}]},
    )

    record = json.loads(destination.read_text(encoding="utf-8"))
    assert record["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert record["agency_key"] == "lametro"
    assert record["provenance"]["feed_status"]["vehicle_count"] == 4
    assert record["prediction_evidence"]["event_count"] == 1
    assert "agency=lametro" in str(destination)


def test_evidence_archive_prunes_expired_date_partitions(tmp_path):
    old_partition = tmp_path / "agency=lametro" / "date=2024-01-01"
    retained_partition = tmp_path / "agency=lametro" / "date=2024-03-20"
    old_partition.mkdir(parents=True)
    retained_partition.mkdir(parents=True)
    (old_partition / "operational_snapshots.jsonl").write_text(
        "{}\n", encoding="utf-8"
    )
    (retained_partition / "operational_snapshots.jsonl").write_text(
        "{}\n", encoding="utf-8"
    )
    archive = EvidenceArchive(tmp_path, retention_days=30)
    now_ms = int(
        datetime(2024, 4, 10, tzinfo=timezone.utc).timestamp() * 1000
    )

    report = archive.prune_history(agency_key="lametro", now_ms=now_ms)

    assert not old_partition.exists()
    assert retained_partition.exists()
    assert report["cutoff_date"] == "2024-03-11"
    assert report["partitions_deleted"] == 1


def test_evidence_archive_retention_is_disabled_by_default(tmp_path):
    old_partition = tmp_path / "agency=lametro" / "date=2024-01-01"
    old_partition.mkdir(parents=True)

    report = EvidenceArchive(tmp_path).prune_history(
        agency_key="lametro", now_ms=1_710_000_000_000
    )

    assert old_partition.exists()
    assert report["enabled"] is False
