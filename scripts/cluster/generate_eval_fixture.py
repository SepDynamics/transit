#!/usr/bin/env python3
"""Generate a deterministic starter trace and label set for evaluation."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cluster.trace_utils import build_trace_from_prometheus_snapshots, write_trace_file

START_MS = 1_719_792_000_000
INTERVAL_MS = 20 * 60 * 1000
SNAPSHOT_COUNT = 72
MODEL_NAME = "NVIDIA A100-SXM4-40GB"
MEM_TOTAL_MB = 40_960.0
POWER_LIMIT_W = 300.0


@dataclass(frozen=True)
class GpuSpec:
    host: str
    gpu_index: int
    uuid: str


GPUS: Sequence[GpuSpec] = (
    GpuSpec("cluster-node-17", 0, "GPU-9c8717aa"),
    GpuSpec("cluster-node-17", 1, "GPU-55f5ae10"),
    GpuSpec("cluster-node-29", 0, "GPU-b0e3c222"),
    GpuSpec("cluster-node-29", 1, "GPU-18ac9349"),
)

INCIDENT_WINDOWS: Sequence[Dict[str, object]] = (
    {"incident_id": "thermal-001", "host": "cluster-node-17", "gpu_index": 0, "incident_class": "thermal_throttle", "start": 18, "end": 23, "expected_action": "throttle"},
    {"incident_id": "thermal-002", "host": "cluster-node-17", "gpu_index": 0, "incident_class": "thermal_throttle", "start": 50, "end": 56, "expected_action": "throttle"},
    {"incident_id": "thermal-003", "host": "cluster-node-17", "gpu_index": 0, "incident_class": "thermal_throttle", "start": 64, "end": 70, "expected_action": "throttle"},
    {"incident_id": "memory-001", "host": "cluster-node-17", "gpu_index": 1, "incident_class": "memory_pressure", "start": 14, "end": 20, "expected_action": "alert"},
    {"incident_id": "memory-002", "host": "cluster-node-17", "gpu_index": 1, "incident_class": "memory_pressure", "start": 36, "end": 43, "expected_action": "alert"},
    {"incident_id": "memory-003", "host": "cluster-node-17", "gpu_index": 1, "incident_class": "memory_pressure", "start": 60, "end": 68, "expected_action": "alert"},
    {"incident_id": "error-001", "host": "cluster-node-29", "gpu_index": 0, "incident_class": "error_burst", "start": 24, "end": 25, "expected_action": "quarantine"},
    {"incident_id": "error-002", "host": "cluster-node-29", "gpu_index": 0, "incident_class": "error_burst", "start": 44, "end": 45, "expected_action": "quarantine"},
    {"incident_id": "error-003", "host": "cluster-node-29", "gpu_index": 0, "incident_class": "error_burst", "start": 68, "end": 69, "expected_action": "quarantine"},
    {"incident_id": "thermal-004", "host": "cluster-node-29", "gpu_index": 1, "incident_class": "thermal_throttle", "start": 30, "end": 35, "expected_action": "throttle"},
)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    base_dir = repo_root / "data" / "evaluation" / "starter"
    snapshot_dir = base_dir / "snapshots"
    trace_path = base_dir / "starter_trace.json"
    labels_path = base_dir / "starter_labels.json"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    snapshots: List[Tuple[int, str]] = []
    counters: Dict[Tuple[str, int], Dict[str, int]] = {
        (gpu.host, gpu.gpu_index): {"xid": 0, "ecc": 0} for gpu in GPUS
    }
    for index in range(SNAPSHOT_COUNT):
        timestamp_ms = START_MS + (index * INTERVAL_MS)
        body = render_snapshot(index=index, counters=counters)
        snapshot_path = snapshot_dir / f"dcgm_snapshot_{timestamp_ms}.prom"
        snapshot_path.write_text(body, encoding="utf-8")
        snapshots.append((timestamp_ms, body))

    trace_payload = build_trace_from_prometheus_snapshots(
        snapshots,
        trace_id="starter-cluster-eval",
        collection_source="dcgm_exporter",
        metadata={"fixture": "starter-evaluation-set", "snapshot_interval_ms": INTERVAL_MS},
        anonymize=False,
    )
    write_trace_file(trace_path, trace_payload)

    labels_payload = {
        "dataset_id": "starter-cluster-eval",
        "generated_at": "2026-04-03T00:00:00+00:00",
        "incidents": [
            {
                "incident_id": str(window["incident_id"]),
                "trace_id": "starter-cluster-eval",
                "host": str(window["host"]),
                "gpu_index": int(window["gpu_index"]),
                "incident_class": str(window["incident_class"]),
                "onset_ms": START_MS + (int(window["start"]) * INTERVAL_MS),
                "end_ms": START_MS + (int(window["end"]) * INTERVAL_MS),
                "expected_action": str(window["expected_action"]),
                "expected_summary": str(window["incident_class"]).replace("_", " "),
            }
            for window in INCIDENT_WINDOWS
        ],
    }
    labels_path.write_text(json.dumps(labels_payload, indent=2), encoding="utf-8")
    print(f"wrote {len(snapshots)} snapshots, trace, and {len(labels_payload['incidents'])} labels under {base_dir}")
    return 0


def render_snapshot(*, index: int, counters: Dict[Tuple[str, int], Dict[str, int]]) -> str:
    lines = [
        "# HELP DCGM_FI_DEV_GPU_UTIL GPU utilization",
        "# TYPE DCGM_FI_DEV_GPU_UTIL gauge",
    ]
    for gpu in GPUS:
        payload = sample_for_gpu(gpu, index=index, counters=counters)
        labels = (
            f'gpu="{gpu.gpu_index}",UUID="{gpu.uuid}",Hostname="{gpu.host}",modelName="{MODEL_NAME}"'
        )
        lines.extend(
            [
                f'DCGM_FI_DEV_GPU_UTIL{{{labels}}} {payload["gpu_util"]:.1f}',
                f'DCGM_FI_DEV_MEM_COPY_UTIL{{{labels}}} {payload["mem_util"]:.1f}',
                f'DCGM_FI_DEV_FB_USED{{{labels}}} {payload["mem_used_mb"]:.1f}',
                f'DCGM_FI_DEV_FB_TOTAL{{{labels}}} {MEM_TOTAL_MB:.1f}',
                f'DCGM_FI_DEV_GPU_TEMP{{{labels}}} {payload["temperature_c"]:.1f}',
                f'DCGM_FI_DEV_POWER_USAGE{{{labels}}} {payload["power_w"] * 1000.0:.1f}',
                f'DCGM_FI_DEV_POWER_MGMT_LIMIT{{{labels}}} {POWER_LIMIT_W * 1000.0:.1f}',
                f'DCGM_FI_DEV_XID_ERRORS{{{labels}}} {payload["xid_errors"]}',
                f'DCGM_FI_DEV_ECC_DBE_VOL_TOTAL{{{labels}}} {payload["ecc_errors"]}',
                f'DCGM_FI_DEV_THERMAL_VIOLATION{{{labels}}} {1 if payload["thermal_violation"] else 0}',
                f'DCGM_FI_DEV_POWER_VIOLATION{{{labels}}} {1 if payload["power_violation"] else 0}',
            ]
        )
    return "\n".join(lines) + "\n"


def sample_for_gpu(
    gpu: GpuSpec,
    *,
    index: int,
    counters: Dict[Tuple[str, int], Dict[str, int]],
) -> Dict[str, float | int | bool]:
    key = (gpu.host, gpu.gpu_index)
    if key == ("cluster-node-17", 0):
        payload = {"gpu_util": 82.0, "mem_ratio": 0.66, "temperature_c": 68.0, "power_w": 242.0}
    elif key == ("cluster-node-17", 1):
        payload = {"gpu_util": 76.0, "mem_ratio": 0.72, "temperature_c": 70.0, "power_w": 236.0}
    elif key == ("cluster-node-29", 0):
        payload = {"gpu_util": 74.0, "mem_ratio": 0.58, "temperature_c": 65.0, "power_w": 225.0}
    else:
        payload = {"gpu_util": 58.0, "mem_ratio": 0.44, "temperature_c": 61.0, "power_w": 182.0}

    thermal_violation = False
    power_violation = False
    for window in incident_windows_for(gpu):
        if int(window["start"]) <= index <= int(window["end"]):
            incident_class = str(window["incident_class"])
            if incident_class == "thermal_throttle":
                payload.update({"gpu_util": 94.0, "mem_ratio": 0.74, "temperature_c": 84.0, "power_w": 294.0})
                thermal_violation = True
                power_violation = True
            elif incident_class == "memory_pressure":
                payload.update({"gpu_util": 88.0, "mem_ratio": 0.958, "temperature_c": 74.0, "power_w": 258.0})
            elif incident_class == "error_burst":
                payload.update({"gpu_util": 61.0, "mem_ratio": 0.63, "temperature_c": 72.0, "power_w": 214.0})
                if index == int(window["start"]):
                    counters[key]["xid"] += 1
                    counters[key]["ecc"] += 8
            break

    mem_used_mb = MEM_TOTAL_MB * float(payload["mem_ratio"])
    return {
        "gpu_util": float(payload["gpu_util"]),
        "mem_util": float(payload["mem_ratio"]) * 100.0,
        "mem_used_mb": mem_used_mb,
        "temperature_c": float(payload["temperature_c"]),
        "power_w": float(payload["power_w"]),
        "xid_errors": counters[key]["xid"],
        "ecc_errors": counters[key]["ecc"],
        "thermal_violation": thermal_violation,
        "power_violation": power_violation,
    }


def incident_windows_for(gpu: GpuSpec) -> Iterable[Dict[str, object]]:
    for window in INCIDENT_WINDOWS:
        if str(window["host"]) == gpu.host and int(window["gpu_index"]) == gpu.gpu_index:
            yield window


if __name__ == "__main__":
    raise SystemExit(main())
