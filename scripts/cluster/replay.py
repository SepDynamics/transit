#!/usr/bin/env python3
"""Replay recorded or synthetic telemetry traces into Valkey."""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cluster.models import TelemetrySample
from scripts.cluster.storage import ClusterStore
from scripts.cluster.trace_utils import load_trace_file, validate_trace_payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay a trace into Cluster Sentinel telemetry keys")
    parser.add_argument("--redis", default=os.getenv("VALKEY_URL", "redis://localhost:6379/0"))
    parser.add_argument("--trace", help="Path to a trace JSON file created by trace_recorder.py")
    parser.add_argument("--speed", type=float, default=8.0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument(
        "--scenario",
        default="auto",
        choices=["auto", "none", "thermal_spike", "memory_pressure", "error_burst", "mixed"],
        help="Replay mutation profile. File traces default to none; demo traces default to mixed.",
    )
    parser.add_argument("--host-prefix", default=os.getenv("REPLAY_HOST_PREFIX", "replay"))
    parser.add_argument("--demo", action="store_true", help="Use the built-in demo trace instead of a file")
    parser.add_argument("--retention", type=int, default=int(os.getenv("REPLAY_SAMPLE_RETENTION", "720")))
    parser.add_argument("--clear-trace", action="store_true", help="Remove any existing replay entities for this trace before replaying.")
    parser.add_argument("--clear-all-replay", action="store_true", help="Remove all replay entities before replaying.")
    parser.add_argument(
        "--cleanup-expired-after-seconds",
        type=int,
        default=int(os.getenv("REPLAY_CLEANUP_EXPIRED_AFTER_SECONDS", "0")),
        help="Remove replay entities older than this age before starting. Disabled when set to 0.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    store = ClusterStore(args.redis)
    stop = False

    def _handle_signal(_signum: int, _frame: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    resolved_scenario = resolve_scenario(args.scenario, trace_path=args.trace, demo=bool(args.demo))
    trace = load_trace(args.trace) if args.trace and not args.demo else build_demo_trace(scenario=resolved_scenario)
    trace_id = str(trace.get("trace_id") or ("demo-trace" if args.demo else Path(args.trace or "replay").stem))
    if args.clear_all_replay:
        for replay_trace_id in store.list_trace_ids():
            store.clear_replay_trace(replay_trace_id)
    if args.cleanup_expired_after_seconds > 0:
        store.purge_expired_entities(scope="replay", stale_after_seconds=max(1, int(args.cleanup_expired_after_seconds)))
    if args.clear_trace:
        store.clear_replay_trace(trace_id)
    while not stop:
        replay_trace(
            store,
            trace,
            trace_id=trace_id,
            scenario=resolved_scenario,
            speed=float(args.speed),
            host_prefix=args.host_prefix,
            retention=max(60, int(args.retention)),
            should_stop=lambda: stop,
        )
        if not args.loop:
            break
    return 0


def load_trace(path: str) -> Dict[str, Any]:
    return load_trace_file(path)


def resolve_scenario(requested: str, *, trace_path: str | None, demo: bool) -> str:
    if requested != "auto":
        return requested
    if trace_path and not demo:
        return "none"
    return "mixed"


def replay_trace(
    store: ClusterStore,
    trace: Dict[str, Any],
    *,
    trace_id: str,
    scenario: str,
    speed: float,
    host_prefix: str,
    retention: int,
    should_stop: Any = None,
) -> int:
    normalized = validate_trace_payload(trace)
    events = flatten_trace(normalized)
    if not events:
        return 0
    last_relative = 0
    emitted = 0
    stop_fn = should_stop if callable(should_stop) else (lambda: False)
    for relative_ms, entity, sample_payload in events:
        if stop_fn():
            break
        delay = max(0.0, (relative_ms - last_relative) / 1000.0 / max(0.1, float(speed)))
        if delay:
            time.sleep(delay)
        last_relative = relative_ms
        sample = apply_scenario(sample_payload, scenario=scenario, progress=(relative_ms / max(1, events[-1][0])))
        sample["timestamp_ms"] = int(time.time() * 1000)
        sample["host"] = f"{host_prefix}-{entity['host']}"
        sample["gpu_index"] = int(entity["gpu_index"])
        sample["uuid"] = str(sample.get("uuid") or f"replay-{trace_id}-{entity['gpu_index']}")
        sample["name"] = str(sample.get("name") or entity.get("sample", {}).get("name") or f"GPU {entity['gpu_index']}")
        sample["source"] = "replay"
        sample["collection_source"] = str(sample.get("collection_source") or "replay")
        sample["trace_id"] = trace_id
        store.record_sample(TelemetrySample.from_mapping(sample), retention=retention)
        store.write_status(
            "ops:replay_status",
            {
                "timestamp_ms": sample["timestamp_ms"],
                "trace_id": trace_id,
                "scenario": scenario,
                "status": "ok",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        emitted += 1
    return emitted


def flatten_trace(trace: Dict[str, Any]) -> List[Tuple[int, Dict[str, Any], Dict[str, Any]]]:
    absolute_events: List[Tuple[int, Dict[str, Any], Dict[str, Any]]] = []
    trace_start_ms: int | None = None
    for entity in trace.get("entities", []):
        for sample in entity.get("samples", []):
            timestamp_ms = int(sample.get("timestamp_ms") or 0)
            if trace_start_ms is None or timestamp_ms < trace_start_ms:
                trace_start_ms = timestamp_ms
            absolute_events.append((timestamp_ms, entity, dict(sample)))
    if trace_start_ms is None:
        return []
    events = [
        (max(0, timestamp_ms - trace_start_ms), entity, sample_payload)
        for timestamp_ms, entity, sample_payload in absolute_events
    ]
    events.sort(key=lambda item: (item[0], str(item[1].get("host")), int(item[1].get("gpu_index") or 0)))
    return events


def apply_scenario(sample: Dict[str, Any], *, scenario: str, progress: float) -> Dict[str, Any]:
    payload = dict(sample)
    mem_total = float(payload.get("mem_total_mb") or 0.0)
    power_limit = float(payload.get("power_limit_w") or 320.0)
    if scenario in {"mixed", "memory_pressure"} and progress >= 0.55:
        payload["mem_used_mb"] = max(float(payload.get("mem_used_mb") or 0.0), mem_total * 0.94 if mem_total else 22_000.0)
        if mem_total > 0:
            payload["mem_util"] = min(99.0, (float(payload["mem_used_mb"]) / mem_total) * 100.0)
    if scenario in {"mixed", "thermal_spike"} and progress >= 0.7:
        payload["temperature_c"] = max(float(payload.get("temperature_c") or 0.0), 84.0)
        payload["power_w"] = max(float(payload.get("power_w") or 0.0), power_limit * 0.97)
        payload["throttle_reasons"] = sorted(set(list(payload.get("throttle_reasons") or []) + ["sw_thermal_slowdown"]))
    if scenario in {"mixed", "error_burst"} and progress >= 0.82:
        payload["xid_errors"] = int(payload.get("xid_errors") or 0) + 1
        payload["ecc_errors"] = int(payload.get("ecc_errors") or 0) + 4
    return payload


def build_demo_trace(*, scenario: str) -> Dict[str, Any]:
    now_ms = int(time.time() * 1000)
    samples: List[Dict[str, Any]] = []
    mem_total = 24_576.0
    for index in range(72):
        timestamp_ms = now_ms + (index * 5_000)
        phase = index / 71.0
        if phase < 0.25:
            gpu_util = 8.0
            mem_used = 2_000.0
            temp = 36.0
            power = 52.0
        elif phase < 0.6:
            gpu_util = 78.0
            mem_used = 15_300.0
            temp = 69.0
            power = 245.0
        elif phase < 0.8:
            gpu_util = 88.0
            mem_used = 21_800.0
            temp = 75.0
            power = 280.0
        else:
            gpu_util = 96.0
            mem_used = 23_700.0
            temp = 82.0
            power = 308.0
        samples.append(
            {
                "timestamp_ms": timestamp_ms,
                "host": "demo-node",
                "gpu_index": 0,
                "uuid": "GPU-DEMO-000",
                "name": "Demo GPU",
                "gpu_util": gpu_util,
                "mem_util": (mem_used / mem_total) * 100.0,
                "mem_used_mb": mem_used,
                "mem_total_mb": mem_total,
                "temperature_c": temp,
                "power_w": power,
                "power_limit_w": 320.0,
                "sm_clock_mhz": 1410.0,
                "mem_clock_mhz": 9500.0,
                "fan_pct": 42.0,
                "ecc_errors": 0,
                "xid_errors": 0,
                "throttle_reasons": [],
                "pcie_tx": 1_200.0 + (index * 18.0),
                "pcie_rx": 850.0 + (index * 12.0),
                "source": "replay",
                "collection_source": "replay",
            }
        )
    if scenario != "none":
        samples = [apply_scenario(sample, scenario=scenario, progress=(idx / max(1, len(samples) - 1))) for idx, sample in enumerate(samples)]
    return validate_trace_payload(
        {
        "trace_id": f"cluster-demo-{scenario}",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "entities": [
            {
                "host": "demo-node",
                "gpu_index": 0,
                "sample": samples[-1],
                "samples": samples,
            }
        ],
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
