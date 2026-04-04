import bz2
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from scripts.cluster.gwdg_dataset import build_label_payload_from_gwdg_archive, build_trace_from_gwdg_archive


ROOT = Path(__file__).resolve().parents[2]


def test_build_trace_from_gwdg_archive_maps_gpu_metrics(tmp_path):
    archive_path = _write_gwdg_fixture_archive(tmp_path / "gwdg-test.zip")

    trace = build_trace_from_gwdg_archive(
        archive_path,
        telemetry_selector="ggpu121_2025-02-10_gpu-error",
        trace_id="gwdg-public-test",
    )

    assert trace["trace_id"] == "gwdg-public-test"
    assert len(trace["entities"]) == 2
    gpu0 = next(entity for entity in trace["entities"] if entity["gpu_index"] == 0)
    assert len(gpu0["samples"]) == 3
    assert gpu0["samples"][-1]["xid_errors"] == 5
    assert gpu0["samples"][-1]["mem_total_mb"] == 40960.0
    assert gpu0["samples"][-1]["power_w"] == 280.0
    gpu1 = next(entity for entity in trace["entities"] if entity["gpu_index"] == 1)
    assert gpu1["samples"][-1]["xid_errors"] == 0


def test_build_label_payload_from_gwdg_archive_uses_incident_window_and_gpu_heuristic(tmp_path):
    archive_path = _write_gwdg_fixture_archive(tmp_path / "gwdg-test.zip")
    trace = build_trace_from_gwdg_archive(archive_path, telemetry_selector="ggpu121_2025-02-10_gpu-error")

    labels = build_label_payload_from_gwdg_archive(
        archive_path,
        telemetry_selector="ggpu121_2025-02-10_gpu-error",
        trace=trace,
    )

    assert len(labels["incidents"]) == 1
    incident = labels["incidents"][0]
    assert incident["gpu_index"] == 0
    assert incident["incident_class"] == "error_burst"
    assert incident["expected_action"] == "quarantine"
    assert incident["onset_ms"] < incident["end_ms"]


def test_import_gwdg_public_cli_writes_trace_and_labels(tmp_path):
    archive_path = _write_gwdg_fixture_archive(tmp_path / "gwdg-test.zip")
    trace_path = tmp_path / "trace.json"
    labels_path = tmp_path / "labels.json"

    subprocess.run(
        [
            sys.executable,
            "scripts/cluster/import_gwdg_public.py",
            "--dataset",
            str(archive_path),
            "--telemetry-file",
            "ggpu121_2025-02-10_gpu-error",
            "--output-trace",
            str(trace_path),
            "--output-labels",
            str(labels_path),
            "--trace-id",
            "cli-public-trace",
        ],
        cwd=ROOT,
        check=True,
    )

    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    assert trace["trace_id"] == "cli-public-trace"
    assert len(labels["incidents"]) == 1


def test_import_gwdg_public_cli_supports_anonymized_trace_and_labels(tmp_path):
    archive_path = _write_gwdg_fixture_archive(tmp_path / "gwdg-test.zip")
    trace_path = tmp_path / "trace-anon.json"
    labels_path = tmp_path / "labels-anon.json"

    subprocess.run(
        [
            sys.executable,
            "scripts/cluster/import_gwdg_public.py",
            "--dataset",
            str(archive_path),
            "--telemetry-file",
            "ggpu121_2025-02-10_gpu-error",
            "--output-trace",
            str(trace_path),
            "--output-labels",
            str(labels_path),
            "--trace-id",
            "cli-public-trace-anon",
            "--anonymize",
        ],
        cwd=ROOT,
        check=True,
    )

    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    assert trace["entities"][0]["host"].startswith("node-")
    assert labels["incidents"][0]["host"] == trace["entities"][0]["host"]


def test_sweep_gwdg_public_cli_writes_ranked_summary(tmp_path):
    archive_path = _write_gwdg_fixture_archive(tmp_path / "gwdg-test.zip")
    cache_dir = tmp_path / "cache"
    summary_path = tmp_path / "summary.json"

    subprocess.run(
        [
            sys.executable,
            "scripts/cluster/sweep_gwdg_public.py",
            "--dataset",
            str(archive_path),
            "--cache-dir",
            str(cache_dir),
            "--output",
            str(summary_path),
            "--episode-cooldown-minutes",
            "240",
        ],
        cwd=ROOT,
        check=True,
    )

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["member_count"] == 1
    assert summary["rows"][0]["telemetry_file"] == "ggpu121_2025-02-10_gpu-error_tidy.csv.bz2"
    assert Path(summary["rows"][0]["report_path"]).exists()


def _write_gwdg_fixture_archive(path: Path) -> Path:
    telemetry_rows = """timeUtc,node,metric,value,gpu,device,uuid,job,instance,modelName,driverVersion
2025-02-09 00:00:00,node_2b08d249c0ba,DCGM_FI_DEV_FB_USED,20480,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:00:00,node_2b08d249c0ba,DCGM_FI_DEV_FB_FREE,20480,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:00:00,node_2b08d249c0ba,DCGM_FI_DEV_GPU_UTIL,70,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:00:00,node_2b08d249c0ba,DCGM_FI_DEV_MEM_COPY_UTIL,60,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:00:00,node_2b08d249c0ba,DCGM_FI_DEV_GPU_TEMP,70,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:00:00,node_2b08d249c0ba,DCGM_FI_DEV_POWER_USAGE,260,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:00:00,node_2b08d249c0ba,DCGM_FI_DEV_XID_ERRORS,0,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:00:00,node_2b08d249c0ba,DCGM_FI_DEV_FB_USED,1024,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:00:00,node_2b08d249c0ba,DCGM_FI_DEV_FB_FREE,39936,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:00:00,node_2b08d249c0ba,DCGM_FI_DEV_GPU_UTIL,5,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:00:00,node_2b08d249c0ba,DCGM_FI_DEV_MEM_COPY_UTIL,3,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:00:00,node_2b08d249c0ba,DCGM_FI_DEV_GPU_TEMP,35,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:00:00,node_2b08d249c0ba,DCGM_FI_DEV_POWER_USAGE,90,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:00:00,node_2b08d249c0ba,DCGM_FI_DEV_XID_ERRORS,0,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:10:00,node_2b08d249c0ba,DCGM_FI_DEV_FB_USED,22528,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:10:00,node_2b08d249c0ba,DCGM_FI_DEV_FB_FREE,18432,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:10:00,node_2b08d249c0ba,DCGM_FI_DEV_GPU_UTIL,77,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:10:00,node_2b08d249c0ba,DCGM_FI_DEV_MEM_COPY_UTIL,65,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:10:00,node_2b08d249c0ba,DCGM_FI_DEV_GPU_TEMP,74,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:10:00,node_2b08d249c0ba,DCGM_FI_DEV_POWER_USAGE,270,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:10:00,node_2b08d249c0ba,DCGM_FI_DEV_XID_ERRORS,1,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:10:00,node_2b08d249c0ba,DCGM_FI_DEV_FB_USED,1024,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:10:00,node_2b08d249c0ba,DCGM_FI_DEV_FB_FREE,39936,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:10:00,node_2b08d249c0ba,DCGM_FI_DEV_GPU_UTIL,4,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:10:00,node_2b08d249c0ba,DCGM_FI_DEV_MEM_COPY_UTIL,2,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:10:00,node_2b08d249c0ba,DCGM_FI_DEV_GPU_TEMP,34,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:10:00,node_2b08d249c0ba,DCGM_FI_DEV_POWER_USAGE,92,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:10:00,node_2b08d249c0ba,DCGM_FI_DEV_XID_ERRORS,0,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:20:00,node_2b08d249c0ba,DCGM_FI_DEV_FB_USED,24576,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:20:00,node_2b08d249c0ba,DCGM_FI_DEV_FB_FREE,16384,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:20:00,node_2b08d249c0ba,DCGM_FI_DEV_GPU_UTIL,81,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:20:00,node_2b08d249c0ba,DCGM_FI_DEV_MEM_COPY_UTIL,71,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:20:00,node_2b08d249c0ba,DCGM_FI_DEV_GPU_TEMP,79,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:20:00,node_2b08d249c0ba,DCGM_FI_DEV_POWER_USAGE,280,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:20:00,node_2b08d249c0ba,DCGM_FI_DEV_XID_ERRORS,5,0,nvidia0,gpu_aaa,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:20:00,node_2b08d249c0ba,DCGM_FI_DEV_FB_USED,1024,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:20:00,node_2b08d249c0ba,DCGM_FI_DEV_FB_FREE,39936,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:20:00,node_2b08d249c0ba,DCGM_FI_DEV_GPU_UTIL,3,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:20:00,node_2b08d249c0ba,DCGM_FI_DEV_MEM_COPY_UTIL,1,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:20:00,node_2b08d249c0ba,DCGM_FI_DEV_GPU_TEMP,33,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:20:00,node_2b08d249c0ba,DCGM_FI_DEV_POWER_USAGE,93,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
2025-02-09 00:20:00,node_2b08d249c0ba,DCGM_FI_DEV_XID_ERRORS,0,1,nvidia1,gpu_bbb,prometheus.scrape.default,inst_1,NVIDIA A100-SXM4-40GB,550.144.03
"""
    incident_rows = """node,incidentDate,incidentDate2,description,category,beforeHours,afterHours,collectStart,collectEnd,windowTotalHours
node_2b08d249c0ba,10 Feb 2025,,gpu error,gpu error/problem,24,2,2025-02-09 00:00:00,2025-02-10 02:00:00,26
"""
    metadata = {
        "inputFile": "ggpu121_2025-02-10_gpu-error.csv.bz2",
        "outputFile": "ggpu121_2025-02-10_gpu-error_tidy.csv.bz2",
        "inputType": "label-heavy-prometheus",
        "node": "node_2b08d249c0ba",
        "timeColumn": "Time",
        "keptColumnCount": 11,
        "keptMetrics": ["DCGM_FI_DEV_FB_USED", "DCGM_FI_DEV_GPU_UTIL", "DCGM_FI_DEV_XID_ERRORS"],
        "notes": "synthetic fixture",
        "schema": "tidy-v1",
    }
    root = "gwdg-gpu-node-telemetry-gpu-detachment-failures-2025-2026-v1.0.0"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{root}/README.md", "# fixture\n")
        archive.writestr(f"{root}/telemetry/ggpu121_2025-02-10_gpu-error_tidy.csv.bz2", bz2.compress(telemetry_rows.encode("utf-8")))
        archive.writestr(f"{root}/metadata/ggpu121_2025-02-10_gpu-error_meta.json", json.dumps(metadata))
        archive.writestr(f"{root}/incident_events.csv", incident_rows)
    return path
