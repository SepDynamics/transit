import subprocess
import sys
from pathlib import Path

from scripts.cluster.evaluation import build_comparison_report, merge_detection_episodes
from scripts.cluster.trace_utils import load_trace_file


ROOT = Path(__file__).resolve().parents[2]
STARTER_DIR = ROOT / "data" / "evaluation" / "starter"


def test_trace_import_converts_snapshot_directory_without_manual_json_editing(tmp_path):
    output_path = tmp_path / "imported_trace.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/cluster/trace_import.py",
            "--input-dir",
            str(STARTER_DIR / "snapshots"),
            "--output",
            str(output_path),
            "--trace-id",
            "imported-starter-trace",
            "--interval-seconds",
            "1200",
        ],
        cwd=ROOT,
        check=True,
    )

    trace = load_trace_file(output_path)

    assert trace["trace_id"] == "imported-starter-trace"
    assert len(trace["entities"]) == 4
    assert all(len(entity["samples"]) == 72 for entity in trace["entities"])


def test_baseline_comparison_scores_the_labeled_incident_set():
    report = build_comparison_report(
        STARTER_DIR / "starter_trace.json",
        STARTER_DIR / "starter_labels.json",
        window_samples=6,
        persistence_windows=3,
    )

    assert report["label_count"] == 10
    assert report["sentinel"]["matched_incident_count"] == 10
    assert report["baseline"]["matched_incident_count"] == 10
    assert len(report["comparison"]["lead_time_by_incident"]) == 10


def test_starter_fixture_is_a_gating_set_not_marketing_copy():
    report = build_comparison_report(
        STARTER_DIR / "starter_trace.json",
        STARTER_DIR / "starter_labels.json",
        window_samples=6,
        persistence_windows=3,
    )

    assert report["sentinel"]["matched_incident_count"] >= report["baseline"]["matched_incident_count"]
    assert report["sentinel"]["extra_alert_count"] <= report["baseline"]["extra_alert_count"]
    memory_incidents = [
        item
        for item in report["comparison"]["lead_time_by_incident"]
        if item["incident_class"] == "memory_pressure"
    ]
    assert memory_incidents
    assert all((item["sentinel_lead_ms"] or 0) >= 0 for item in memory_incidents)


def test_coarse_public_labels_do_not_produce_lead_time_claims():
    labels = {
        "dataset_id": "coarse-labels",
        "incidents": [
            {
                "incident_id": "coarse-001",
                "trace_id": "starter-cluster-eval",
                "host": "cluster-node-17",
                "gpu_index": 0,
                "incident_class": "thermal_throttle",
                "onset_ms": 1719792000000,
                "end_ms": 1719878400000,
                "onset_precision": "coarse_window",
                "label_granularity": "node_window",
                "expected_action": "throttle",
            }
        ],
    }

    report = build_comparison_report(
        STARTER_DIR / "starter_trace.json",
        labels,
        window_samples=6,
        persistence_windows=3,
    )

    assert report["sentinel"]["lead_time_evaluable_count"] == 0
    assert report["baseline"]["lead_time_evaluable_count"] == 0
    assert report["comparison"]["lead_time_evaluable_count"] == 0
    assert report["comparison"]["lead_time_by_incident"][0]["sentinel_lead_ms"] is None
    assert report["comparison"]["lead_time_by_incident"][0]["lead_time_evaluable"] is False


def test_episode_cooldown_merges_repeated_same_class_detections():
    detections = [
        {
            "engine": "sentinel",
            "host": "node-a",
            "gpu_index": 0,
            "incident_class": "unstable",
            "action": "alert",
            "timestamp_ms": 1_700_000_000_000,
        },
        {
            "engine": "sentinel",
            "host": "node-a",
            "gpu_index": 0,
            "incident_class": "unstable",
            "action": "alert",
            "timestamp_ms": 1_700_000_900_000,
        },
        {
            "engine": "sentinel",
            "host": "node-a",
            "gpu_index": 0,
            "incident_class": "unstable",
            "action": "alert",
            "timestamp_ms": 1_700_007_300_000,
        },
        {
            "engine": "sentinel",
            "host": "node-a",
            "gpu_index": 0,
            "incident_class": "error_burst",
            "action": "quarantine",
            "timestamp_ms": 1_700_000_600_000,
        },
    ]

    merged = merge_detection_episodes(detections, cooldown_ms=60 * 60 * 1000)

    assert len(merged) == 3
    assert [item["incident_class"] for item in merged] == ["unstable", "error_burst", "unstable"]
