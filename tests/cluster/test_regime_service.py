from scripts.cluster.models import TelemetrySample
from scripts.cluster.regime_service import recommended_action, score_gpu_window


def build_sample(
    index: int,
    *,
    gpu_util: float = 94.0,
    mem_used_mb: float = 39_000.0,
    mem_total_mb: float = 40_960.0,
    temperature_c: float = 84.0,
    power_w: float = 292.0,
    power_limit_w: float = 300.0,
    throttle_reasons=None,
) -> TelemetrySample:
    return TelemetrySample(
        timestamp_ms=1_700_000_000_000 + (index * 5_000),
        host="node-a",
        gpu_index=0,
        uuid="GPU-123",
        name="NVIDIA A100",
        gpu_util=gpu_util,
        mem_util=(mem_used_mb / mem_total_mb) * 100.0,
        mem_used_mb=mem_used_mb,
        mem_total_mb=mem_total_mb,
        temperature_c=temperature_c,
        power_w=power_w,
        power_limit_w=power_limit_w,
        sm_clock_mhz=1410.0,
        mem_clock_mhz=9800.0,
        fan_pct=72.0,
        ecc_errors=0,
        xid_errors=0,
        throttle_reasons=list(throttle_reasons or ["sw_thermal_slowdown"]),
        pcie_tx=1800.0,
        pcie_rx=900.0,
        source="live",
        collection_source="nvml",
    )


def test_score_gpu_window_detects_thermal_throttle():
    samples = [build_sample(index) for index in range(60)]

    payload = score_gpu_window(samples)
    action = recommended_action(
        regime=payload["regime"],
        hazard=payload["hazard"],
        repetitions=1,
        reasons=payload["reasons"],
    )

    assert payload["regime"] == "thermal_throttle"
    assert payload["hazard"] >= 0.8
    assert payload["scoring_backend"] in {"native", "fallback"}
    assert 0.0 <= payload["confidence"] <= 1.0
    assert "clock_throttle_detected" in payload["reasons"]
    assert payload["provenance"]["top_factors"][0]["factor"] in {
        "thermal_pressure",
        "clock_throttle",
        "power_pressure",
    }
    assert action == "throttle"


def test_score_gpu_window_ignores_gpu_idle_throttle_reason():
    samples = [
        build_sample(
            index,
            gpu_util=12.0,
            mem_used_mb=1_800.0,
            temperature_c=55.0,
            power_w=45.0,
            throttle_reasons=["gpu_idle"],
        )
        for index in range(12)
    ]

    payload = score_gpu_window(samples)

    assert payload["regime"] == "idle"
    assert "clock_throttle_detected" not in payload["reasons"]
    assert payload["confidence"] > 0.5


def test_score_gpu_window_detects_recent_memory_pressure_before_full_window_average():
    samples = [
        build_sample(
            index,
            gpu_util=76.0 if index < 3 else 88.0,
            mem_used_mb=29_491.2 if index < 3 else 39_239.68,
            mem_total_mb=40_960.0,
            temperature_c=70.0 if index < 3 else 74.0,
            power_w=236.0 if index < 3 else 258.0,
            throttle_reasons=["gpu_idle"],
        )
        for index in range(6)
    ]

    payload = score_gpu_window(samples)
    action = recommended_action(
        regime=payload["regime"],
        hazard=payload["hazard"],
        repetitions=1,
        reasons=payload["reasons"],
    )

    assert payload["regime"] == "memory_pressure"
    assert payload["metrics"]["recent_high_mem_samples"] == 3
    assert "memory_tail_near_limit" in payload["reasons"]
    assert payload["provenance"]["signal_agreement"] > 0.4
    assert action == "alert"


def test_recommended_action_does_not_escalate_rupture_only_unstable_repetition():
    action = recommended_action(
        regime="unstable",
        hazard=0.19,
        repetitions=3,
        reasons=["unstable", "regime_rupture"],
    )

    assert action == "watch"


def test_recommended_action_keeps_low_hazard_memory_pressure_as_context_without_hot_tail():
    action = recommended_action(
        regime="memory_pressure",
        hazard=0.12,
        repetitions=1,
        reasons=["memory_pressure", "memory_footprint_near_limit", "memory_pressure_persistence"],
    )

    assert action == "watch"
