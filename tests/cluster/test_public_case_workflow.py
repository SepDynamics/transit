import json
import subprocess
import sys
from pathlib import Path

from scripts.cluster.evaluation import (
    build_comparison_report,
    grade_public_case_report,
    grade_public_control_report,
)


ROOT = Path(__file__).resolve().parents[2]


def test_public_case_grade_and_render_summary(tmp_path):
    report_path = tmp_path / "public_report.json"
    labels_path = tmp_path / "public_labels.json"
    trace_path = tmp_path / "public_trace.json"
    grade_path = tmp_path / "public_grade.json"
    summary_path = tmp_path / "public_summary.md"

    trace = {
        "trace_id": "public-trace",
        "entities": [
            {
                "host": "node-public",
                "gpu_index": 2,
                "samples": [
                    {
                        "timestamp_ms": 1_700_000_000_000,
                        "host": "node-public",
                        "gpu_index": 2,
                        "uuid": "GPU-002",
                        "name": "GPU 2",
                        "gpu_util": 60.0,
                        "mem_util": 72.0,
                        "mem_used_mb": 29_000.0,
                        "mem_total_mb": 40_960.0,
                        "temperature_c": 72.0,
                        "power_w": 235.0,
                        "power_limit_w": 300.0,
                        "ecc_errors": 0,
                        "xid_errors": 0,
                        "throttle_reasons": [],
                        "source": "live",
                        "collection_source": "gwdg_zenodo",
                        "trace_id": "public-trace",
                    }
                ],
            }
        ],
    }
    labels = {
        "dataset_id": "public-trace-labels",
        "metadata": {
            "heuristic_gpu_indexes": [2, 3],
        },
        "incidents": [
            {
                "incident_id": "public-001",
                "trace_id": "public-trace",
                "host": "node-public",
                "gpu_index": 2,
                "incident_class": "unstable",
                "onset_ms": 1_700_000_000_000,
                "end_ms": 1_700_000_600_000,
                "onset_precision": "coarse_window",
                "label_granularity": "node_window",
                "expected_action": "drain",
            }
        ],
    }
    report = {
        "trace_id": "public-trace",
        "dataset_id": "public-trace-labels",
        "sentinel": {
            "detections": [
                {
                    "engine": "sentinel",
                    "trace_id": "public-trace",
                    "host": "node-public",
                    "gpu_index": 2,
                    "incident_class": "memory_pressure",
                    "timestamp_ms": 1_700_000_060_000,
                    "action": "watch",
                },
                {
                    "engine": "sentinel",
                    "trace_id": "public-trace",
                    "host": "node-public",
                    "gpu_index": 2,
                    "incident_class": "unstable",
                    "timestamp_ms": 1_700_000_120_000,
                    "action": "drain",
                },
                {
                    "engine": "sentinel",
                    "trace_id": "public-trace",
                    "host": "node-public",
                    "gpu_index": 2,
                    "incident_class": "error_burst",
                    "timestamp_ms": 1_700_000_240_000,
                    "action": "quarantine",
                },
            ],
            "extra_alerts": [
                {
                    "engine": "sentinel",
                    "trace_id": "public-trace",
                    "host": "node-public",
                    "gpu_index": 2,
                    "incident_class": "memory_pressure",
                    "timestamp_ms": 1_700_000_060_000,
                    "action": "watch",
                },
                {
                    "engine": "sentinel",
                    "trace_id": "public-trace",
                    "host": "node-public",
                    "gpu_index": 2,
                    "incident_class": "error_burst",
                    "timestamp_ms": 1_700_000_240_000,
                    "action": "quarantine",
                },
            ],
        },
        "baseline": {
            "detections": [
                {
                    "engine": "baseline",
                    "trace_id": "public-trace",
                    "host": "node-public",
                    "gpu_index": 2,
                    "incident_class": "error_burst",
                    "timestamp_ms": 1_700_000_240_000,
                    "action": "quarantine",
                }
            ],
            "extra_alerts": [
                {
                    "engine": "baseline",
                    "trace_id": "public-trace",
                    "host": "node-public",
                    "gpu_index": 2,
                    "incident_class": "error_burst",
                    "timestamp_ms": 1_700_000_240_000,
                    "action": "quarantine",
                }
            ],
        },
    }

    trace_path.write_text(json.dumps(trace, indent=2), encoding="utf-8")
    labels_path.write_text(json.dumps(labels, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    grade = grade_public_case_report(report_path, labels=labels_path, trace=trace_path)
    assert grade["status"] == "pass"
    assert grade["criteria"]["claim_supported"]["passed"] is True
    assert grade["per_incident"][0]["sentinel"]["context_classes"] == ["error_burst", "memory_pressure"]

    subprocess.run(
        [
            sys.executable,
            "scripts/cluster/grade_public_case.py",
            "--report",
            str(report_path),
            "--labels",
            str(labels_path),
            "--trace",
            str(trace_path),
            "--output",
            str(grade_path),
            "--strict",
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "scripts/cluster/render_public_case_summary.py",
            "--report",
            str(report_path),
            "--labels",
            str(labels_path),
            "--trace",
            str(trace_path),
            "--replay-command",
            "python3 scripts/cluster/replay.py --trace output/public/case.json --clear-trace",
            "--output",
            str(summary_path),
        ],
        cwd=ROOT,
        check=True,
    )

    summary = summary_path.read_text(encoding="utf-8")
    assert "Cluster Sentinel Public Case Summary" in summary
    assert "coarse node-window event labels" in summary
    assert "--scenario none" in summary


def test_public_control_grade_and_render_summary(tmp_path):
    report_path = tmp_path / "control_report.json"
    trace_path = tmp_path / "control_trace.json"
    grade_path = tmp_path / "control_grade.json"
    summary_path = tmp_path / "control_summary.md"

    trace = {
        "trace_id": "public-control",
        "entities": [
            {
                "host": "node-public",
                "gpu_index": 0,
                "samples": [
                    {
                        "timestamp_ms": 1_700_000_000_000,
                        "host": "node-public",
                        "gpu_index": 0,
                        "uuid": "GPU-000",
                        "name": "GPU 0",
                        "gpu_util": 44.0,
                        "mem_util": 30.0,
                        "mem_used_mb": 12_000.0,
                        "mem_total_mb": 40_960.0,
                        "temperature_c": 61.0,
                        "power_w": 180.0,
                        "power_limit_w": 300.0,
                        "ecc_errors": 0,
                        "xid_errors": 0,
                        "throttle_reasons": [],
                        "source": "live",
                        "collection_source": "gwdg_zenodo",
                        "trace_id": "public-control",
                    }
                ],
            }
        ],
    }
    report = {
        "trace_id": "public-control",
        "dataset_id": "public-control-labels",
        "sentinel": {
            "detection_count": 0,
            "extra_alert_count": 0,
        },
        "baseline": {
            "detection_count": 3,
            "extra_alert_count": 3,
        },
    }

    trace_path.write_text(json.dumps(trace, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    grade = grade_public_control_report(report_path, trace=trace_path)
    assert grade["status"] == "pass"
    assert grade["criteria"]["sentinel_quiet"]["passed"] is True
    assert grade["criteria"]["baseline_detected"]["passed"] is True

    subprocess.run(
        [
            sys.executable,
            "scripts/cluster/grade_public_control.py",
            "--report",
            str(report_path),
            "--trace",
            str(trace_path),
            "--output",
            str(grade_path),
            "--strict",
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "scripts/cluster/render_public_control_summary.py",
            "--report",
            str(report_path),
            "--trace",
            str(trace_path),
            "--replay-command",
            "python3 scripts/cluster/replay.py --trace output/public/control.json --clear-trace",
            "--output",
            str(summary_path),
        ],
        cwd=ROOT,
        check=True,
    )

    summary = summary_path.read_text(encoding="utf-8")
    assert "Cluster Sentinel Public Control Summary" in summary
    assert "Sentinel stays quiet on the control slice" in summary
    assert "--scenario none" in summary


def test_public_node_window_merge_preserves_multi_gpu_localization():
    base_timestamp = 1_700_000_000_000
    trace = {
        "trace_id": "public-node-window",
        "entities": [
            {
                "host": "node-public",
                "gpu_index": gpu_index,
                "samples": [
                    {
                        "timestamp_ms": base_timestamp + (step * 60_000),
                        "host": "node-public",
                        "gpu_index": gpu_index,
                        "uuid": f"GPU-{gpu_index:03d}",
                        "name": f"GPU {gpu_index}",
                        "gpu_util": 96.0,
                        "mem_util": 80.0,
                        "mem_used_mb": 32_000.0,
                        "mem_total_mb": 40_960.0,
                        "temperature_c": 71.0,
                        "power_w": 240.0,
                        "power_limit_w": 300.0,
                        "ecc_errors": 0,
                        "xid_errors": 5 if step >= 5 else 0,
                        "throttle_reasons": [],
                        "source": "replay",
                        "collection_source": "gwdg_zenodo",
                        "trace_id": "public-node-window",
                    }
                    for step in range(6)
                ],
            }
            for gpu_index in (2, 3)
        ],
    }
    labels = {
        "dataset_id": "public-node-window-labels",
        "metadata": {
            "heuristic_gpu_indexes": [2, 3],
        },
        "incidents": [
            {
                "incident_id": "public-node-window-001",
                "trace_id": "public-node-window",
                "host": "node-public",
                "gpu_index": 2,
                "incident_class": "error_burst",
                "onset_ms": base_timestamp,
                "end_ms": base_timestamp + (6 * 60_000),
                "onset_precision": "coarse_window",
                "label_granularity": "node_window",
                "expected_action": "quarantine",
            }
        ],
    }

    report = build_comparison_report(
        trace,
        labels,
        window_samples=6,
        persistence_windows=1,
        episode_cooldown_ms=240 * 60 * 1000,
    )
    sentinel_detections = report["sentinel"]["detections"]
    assert len(sentinel_detections) == 1
    assert sentinel_detections[0]["gpu_indexes"] == [2, 3]

    grade = grade_public_case_report(report, labels=labels, trace=trace)
    incident = grade["per_incident"][0]
    assert incident["sentinel"]["localized_gpu_indexes"] == [2, 3]
    assert incident["sentinel"]["gpu_localized"] is True
