import json

from scripts.transit.evidence import EVIDENCE_SCHEMA_VERSION, EvidenceArchive


def test_evidence_archive_writes_versioned_partitioned_snapshot(tmp_path):
    destination = EvidenceArchive(tmp_path).append_snapshot(
        {
            "health": {"timestamp_ms": 1_710_000_000_000, "status": "ok"},
            "feed_status": {"status": "ok", "vehicle_count": 4},
            "entities": {"vehicles": [{"entity_id": "vehicle:1"}]},
            "regimes": {"regimes": []},
            "incidents": {"incidents": []},
        },
        agency_key="lametro",
        archive_manifest={"feeds": [{"name": "alerts", "status": "archived"}]},
    )

    record = json.loads(destination.read_text(encoding="utf-8"))
    assert record["schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert record["agency_key"] == "lametro"
    assert record["provenance"]["feed_status"]["vehicle_count"] == 4
    assert "agency=lametro" in str(destination)
