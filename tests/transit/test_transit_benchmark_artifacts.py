import json
from pathlib import Path

from scripts.transit.benchmark_artifacts import (
    TransitBenchmarkArtifactConfig,
    TransitBenchmarkArtifactService,
)


def test_transit_benchmark_artifacts_service_writes_expected_bundle(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    case_pack_root = repo_root / "data" / "case-packs" / "mbta" / "daytime_red_line_delay_spike"
    output_root = tmp_path / "artifacts" / "benchmarks"

    manifest = TransitBenchmarkArtifactService(
        TransitBenchmarkArtifactConfig(
            archive_root=case_pack_root,
            labels_root=case_pack_root / "labels",
            output_root=output_root,
            artifact_name="mbta-delay-pack",
            max_snapshots=None,
        )
    ).run_once()

    artifact_dir = output_root / "mbta-delay-pack"
    written_manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    summary_markdown = (artifact_dir / "calibration_summary.md").read_text(encoding="utf-8")
    case_pack_files = written_manifest["case_packs"][0]["files"]

    assert manifest["artifact_name"] == "mbta-delay-pack"
    assert manifest["mode"] == "suite"
    assert written_manifest["summary"]["value_case_supported"] is True
    assert (artifact_dir / "calibration_report.json").exists()
    assert (artifact_dir / "calibration_summary.md").exists()
    assert (artifact_dir / case_pack_files["archive_report"]).exists()
    assert (artifact_dir / case_pack_files["calibration_report"]).exists()
    assert "PASS" in summary_markdown
