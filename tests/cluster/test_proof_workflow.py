import json
import subprocess
import sys
from pathlib import Path

from scripts.cluster.evaluation import build_comparison_report


ROOT = Path(__file__).resolve().parents[2]
STARTER_DIR = ROOT / "data" / "evaluation" / "starter"
STARTER_TRACE = STARTER_DIR / "starter_trace.json"
STARTER_LABELS = STARTER_DIR / "starter_labels.json"


def test_label_trace_cli_can_init_add_and_validate_exact_labels(tmp_path):
    seed_incident = json.loads(STARTER_LABELS.read_text(encoding="utf-8"))["incidents"][0]
    labels_path = tmp_path / "exact_labels.json"

    subprocess.run(
        [
            sys.executable,
            "scripts/cluster/label_trace.py",
            "init",
            "--trace",
            str(STARTER_TRACE),
            "--output",
            str(labels_path),
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "scripts/cluster/label_trace.py",
            "add",
            "--trace",
            str(STARTER_TRACE),
            "--labels",
            str(labels_path),
            "--incident-id",
            str(seed_incident["incident_id"]),
            "--host",
            str(seed_incident["host"]),
            "--gpu-index",
            str(seed_incident["gpu_index"]),
            "--incident-class",
            str(seed_incident["incident_class"]),
            "--onset",
            str(seed_incident["onset_ms"]),
            "--end",
            str(seed_incident["end_ms"]),
            "--expected-action",
            str(seed_incident["expected_action"]),
            "--expected-summary",
            str(seed_incident["expected_summary"]),
            "--onset-precision",
            "exact",
            "--label-granularity",
            "gpu_exact",
        ],
        cwd=ROOT,
        check=True,
    )

    payload = json.loads(labels_path.read_text(encoding="utf-8"))
    assert len(payload["incidents"]) == 1
    assert payload["incidents"][0]["onset_precision"] == "exact"
    assert payload["incidents"][0]["label_granularity"] == "gpu_exact"

    validation = subprocess.run(
        [
            sys.executable,
            "scripts/cluster/label_trace.py",
            "validate",
            "--trace",
            str(STARTER_TRACE),
            "--labels",
            str(labels_path),
            "--json",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    parsed = json.loads(validation.stdout)
    assert parsed["ok"] is True
    assert parsed["exact_incident_count"] == 1


def test_label_trace_validate_rejects_out_of_range_exact_labels(tmp_path):
    payload = {
        "dataset_id": "invalid-exact",
        "incidents": [
            {
                "incident_id": "invalid-001",
                "trace_id": "starter-cluster-eval",
                "host": "cluster-node-17",
                "gpu_index": 0,
                "incident_class": "thermal_throttle",
                "onset_ms": 1,
                "end_ms": 2,
                "onset_precision": "exact",
                "label_granularity": "gpu_exact",
                "expected_action": "throttle",
            }
        ],
    }
    labels_path = tmp_path / "invalid_labels.json"
    labels_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/cluster/label_trace.py",
            "validate",
            "--trace",
            str(STARTER_TRACE),
            "--labels",
            str(labels_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "exact onset_ms is outside trace range" in result.stdout


def test_grade_proof_and_render_summary_for_starter_report(tmp_path):
    report_path = tmp_path / "starter_report.json"
    grade_path = tmp_path / "starter_grade.json"
    summary_path = tmp_path / "starter_summary.md"
    report = build_comparison_report(
        STARTER_TRACE,
        STARTER_LABELS,
        window_samples=6,
        persistence_windows=3,
    )
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    subprocess.run(
        [
            sys.executable,
            "scripts/cluster/grade_proof.py",
            "--report",
            str(report_path),
            "--trace",
            str(STARTER_TRACE),
            "--output",
            str(grade_path),
            "--strict",
        ],
        cwd=ROOT,
        check=True,
    )
    grade = json.loads(grade_path.read_text(encoding="utf-8"))
    assert grade["status"] == "pass"
    assert grade["criteria"]["matched_incidents"]["passed"] is True
    assert grade["criteria"]["extra_alerts"]["passed"] is True
    assert grade["criteria"]["lead_or_action_quality"]["passed"] is True
    assert grade["criteria"]["replay_ready"]["passed"] is True

    subprocess.run(
        [
            sys.executable,
            "scripts/cluster/render_proof_summary.py",
            "--report",
            str(report_path),
            "--trace",
            str(STARTER_TRACE),
            "--labels",
            str(STARTER_LABELS),
            "--replay-command",
            "python3 scripts/cluster/replay.py --trace data/evaluation/starter/starter_trace.json --clear-trace",
            "--output",
            str(summary_path),
        ],
        cwd=ROOT,
        check=True,
    )
    summary = summary_path.read_text(encoding="utf-8")
    assert "Cluster Sentinel Proof Summary" in summary
    assert "Verdict: `PASS`" in summary
    assert "starter-cluster-eval" in summary
    assert "--scenario none" in summary
