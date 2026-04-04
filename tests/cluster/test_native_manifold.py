import importlib
import json
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from scripts.cluster.models import TelemetrySample
from scripts.cluster.regime_service import analyze_window_fallback, encode_samples


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def native_manifold_module(tmp_path_factory):
    build_dir = tmp_path_factory.mktemp("native-manifold")
    ext_suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    output_path = build_dir / f"manifold_engine{ext_suffix}"
    subprocess.run(
        ["sh", "scripts/build_manifold_engine.sh", str(output_path)],
        cwd=ROOT,
        check=True,
    )
    sys.modules.pop("manifold_engine", None)
    sys.path.insert(0, str(build_dir))
    try:
        importlib.invalidate_caches()
        yield importlib.import_module("manifold_engine")
    finally:
        sys.modules.pop("manifold_engine", None)
        sys.path.remove(str(build_dir))


def test_native_manifold_matches_fallback_on_golden_windows(native_manifold_module):
    for name, samples in {
        "stable": build_window("stable"),
        "oscillating": build_window("oscillating"),
        "regime_shift": build_window("regime_shift"),
    }.items():
        encoded_bytes, meta = encode_samples(samples)
        payload = json.loads(
            native_manifold_module.analyze_bytes(
                encoded_bytes,
                len(encoded_bytes),
                len(encoded_bytes),
                3,
            )
        )
        window = payload["windows"][0]
        native = {
            "coherence": float(window["metrics"]["coherence"]),
            "stability": float(window["metrics"]["stability"]),
            "entropy": float(window["metrics"]["entropy"]),
            "rupture": float(window["metrics"]["rupture"]),
            "hazard": float(window["lambda_hazard"]),
        }
        fallback = analyze_window_fallback(encoded_bytes, meta["severity_series"])

        if name == "stable":
            assert native["stability"] > 0.95
            assert native["rupture"] < 0.05
        elif name == "oscillating":
            assert native["stability"] < 0.1
            assert native["coherence"] < 0.1
        else:
            assert native["rupture"] > 0.9
            assert native["stability"] > 0.8

        for metric in ("coherence", "stability", "entropy", "rupture", "hazard"):
            assert native[metric] == pytest.approx(fallback[metric], abs=0.06), (name, metric, native, fallback)


def build_window(kind: str) -> list[TelemetrySample]:
    def make_sample(
        index: int,
        *,
        gpu_util: float,
        mem_ratio: float,
        temperature_c: float,
        power_w: float,
        throttle_reasons=None,
        xid_errors: int = 0,
        ecc_errors: int = 0,
    ) -> TelemetrySample:
        return TelemetrySample(
            timestamp_ms=1_700_000_000_000 + (index * 5_000),
            host="node-a",
            gpu_index=0,
            uuid="GPU-TEST-001",
            name="NVIDIA A100",
            gpu_util=gpu_util,
            mem_util=mem_ratio * 100.0,
            mem_used_mb=mem_ratio * 40_960.0,
            mem_total_mb=40_960.0,
            temperature_c=temperature_c,
            power_w=power_w,
            power_limit_w=300.0,
            sm_clock_mhz=1410.0,
            mem_clock_mhz=9800.0,
            fan_pct=55.0,
            ecc_errors=ecc_errors,
            xid_errors=xid_errors,
            throttle_reasons=list(throttle_reasons or []),
            source="live",
            collection_source="test",
        )

    if kind == "stable":
        return [make_sample(index, gpu_util=82.0, mem_ratio=0.70, temperature_c=68.0, power_w=242.0) for index in range(24)]
    if kind == "oscillating":
        return [
            make_sample(
                index,
                gpu_util=95.0 if index % 2 else 20.0,
                mem_ratio=0.55 if index % 2 else 0.20,
                temperature_c=78.0 if index % 2 else 66.0,
                power_w=260.0 if index % 2 else 110.0,
            )
            for index in range(24)
        ]
    if kind == "regime_shift":
        return [
            make_sample(
                index,
                gpu_util=30.0 if index < 12 else 96.0,
                mem_ratio=0.25 if index < 12 else 0.95,
                temperature_c=58.0 if index < 12 else 84.0,
                power_w=120.0 if index < 12 else 295.0,
                throttle_reasons=["sw_thermal_slowdown"] if index >= 12 else [],
                xid_errors=0 if index < 18 else 1,
                ecc_errors=0 if index < 18 else 4,
            )
            for index in range(24)
        ]
    raise ValueError(f"unsupported window kind: {kind}")
