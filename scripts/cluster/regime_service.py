#!/usr/bin/env python3
"""Telemetry window regime scoring for Cluster Sentinel."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import signal
import statistics
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cluster.models import RegimeRecord, TelemetrySample, clamp, isoformat_ms
from scripts.cluster.storage import ClusterStore

logger = logging.getLogger("cluster-regime-service")

MEANINGFUL_THROTTLE_REASONS = {
    "sw_power_cap",
    "hw_slowdown",
    "sync_boost",
    "sw_thermal_slowdown",
    "hw_thermal_slowdown",
    "hw_power_brake",
    "power_cap",
    "thermal_violation",
    "board_limit",
}

COMPONENT_WEIGHTS: Dict[str, float] = {
    "structural_instability": 0.24,
    "thermal_pressure": 0.18,
    "power_pressure": 0.12,
    "memory_pressure": 0.14,
    "clock_throttle": 0.12,
    "error_pressure": 0.22,
    "utilization_volatility": 0.10,
}

COMPONENT_LABELS: Dict[str, str] = {
    "structural_instability": "Structural instability",
    "thermal_pressure": "Thermal pressure",
    "power_pressure": "Power pressure",
    "memory_pressure": "Memory pressure",
    "clock_throttle": "Clock throttle",
    "error_pressure": "Error pressure",
    "utilization_volatility": "Utilization volatility",
}

REGIME_COMPONENT_EXPECTATIONS: Dict[str, Sequence[str]] = {
    "error_burst": ("error_pressure", "structural_instability"),
    "thermal_throttle": ("thermal_pressure", "clock_throttle", "power_pressure"),
    "memory_pressure": ("memory_pressure",),
    "unstable": ("structural_instability", "utilization_volatility"),
    "degraded": ("structural_instability", "thermal_pressure", "memory_pressure", "error_pressure"),
    "saturated": ("memory_pressure", "power_pressure"),
}

COLLECTION_SOURCE_QUALITY: Dict[str, float] = {
    "dcgm_exporter": 1.0,
    "nvml": 0.95,
    "nvidia_smi": 0.70,
    "replay": 0.90,
    "test": 0.90,
    "test-collector": 0.90,
    "test-collector-sequence": 0.90,
    "unknown": 0.60,
}


@dataclass
class RegimeServiceConfig:
    redis_url: str
    window_samples: int
    loop_seconds: float
    history_retention: int
    signature_retention_minutes: int


class RegimeService:
    def __init__(self, config: RegimeServiceConfig) -> None:
        self.cfg = config
        self.store = ClusterStore(config.redis_url)
        self._stop = False
        self._signature_history: Dict[str, Dict[str, Deque[int]]] = defaultdict(lambda: defaultdict(deque))
        self._last_emitted_ts: Dict[str, int] = {}

    def run(self) -> None:
        logger.info("Cluster regime service starting")
        while not self._stop:
            started = time.time()
            try:
                self.run_once()
            except Exception:
                logger.exception("regime iteration failed")
            elapsed = time.time() - started
            time.sleep(max(0.2, self.cfg.loop_seconds - elapsed))

    def run_once(self) -> List[Dict[str, Any]]:
        payloads: List[Dict[str, Any]] = []
        for entity in self.store.list_entities(scope="all"):
            host = entity["host"]
            gpu_index = entity["gpu_index"]
            samples = self.store.get_recent_samples(host, gpu_index, limit=self.cfg.window_samples)
            if len(samples) < self.cfg.window_samples:
                continue
            telemetry = [TelemetrySample.from_mapping(sample) for sample in samples]
            last_ts = telemetry[-1].timestamp_ms
            entity_id = f"{host}:{gpu_index}"
            if self._last_emitted_ts.get(entity_id) == last_ts:
                continue
            base_payload = score_gpu_window(telemetry)
            repetitions = self._update_signature_history(entity_id, base_payload["signature"], last_ts)
            action = recommended_action(
                regime=str(base_payload["regime"]),
                hazard=float(base_payload["hazard"]),
                repetitions=repetitions,
                reasons=list(base_payload["reasons"]),
            )
            record = RegimeRecord(
                timestamp_ms=last_ts,
                host=telemetry[-1].host,
                gpu_index=telemetry[-1].gpu_index,
                uuid=telemetry[-1].uuid,
                name=telemetry[-1].name,
                source=telemetry[-1].source,
                collection_source=telemetry[-1].collection_source,
                trace_id=telemetry[-1].trace_id,
                regime=str(base_payload["regime"]),
                hazard=float(base_payload["hazard"]),
                repetitions=repetitions,
                action=action,
                scoring_backend=str(base_payload["scoring_backend"]),
                confidence=float(base_payload["confidence"]),
                signature=str(base_payload["signature"]),
                coherence=float(base_payload["coherence"]),
                stability=float(base_payload["stability"]),
                entropy=float(base_payload["entropy"]),
                rupture=float(base_payload["rupture"]),
                reasons=list(base_payload["reasons"]),
                provenance=dict(base_payload["provenance"]),
                metrics=dict(base_payload["metrics"]),
            )
            payload = record.to_json()
            payload["updated_at"] = isoformat_ms(last_ts)
            self.store.write_regime(payload, retention=self.cfg.history_retention)
            payloads.append(payload)
            self._last_emitted_ts[entity_id] = last_ts
        self.store.write_status(
            "ops:regime_status",
            {
                "timestamp_ms": int(time.time() * 1000),
                "updated_at": isoformat_ms(),
                "status": "ok",
                "emitted": len(payloads),
            },
        )
        return payloads

    def stop(self) -> None:
        self._stop = True

    def _update_signature_history(self, entity_id: str, signature: str, timestamp_ms: int) -> int:
        history = self._signature_history[entity_id][signature]
        history.append(timestamp_ms)
        cutoff = timestamp_ms - (self.cfg.signature_retention_minutes * 60 * 1000)
        while history and history[0] < cutoff:
            history.popleft()
        return len(history)


def score_gpu_window(samples: Sequence[TelemetrySample]) -> Dict[str, Any]:
    if not samples:
        raise ValueError("samples are required")
    encoded_bytes, feature_meta = encode_samples(samples)
    structural = analyze_window(encoded_bytes, feature_meta["severity_series"])
    scoring_backend = str(structural.get("backend") or "fallback")
    avg_gpu_util = _mean(sample.gpu_util for sample in samples)
    avg_mem_util = _mean(sample.mem_util for sample in samples)
    avg_mem_ratio = _mean(
        (sample.mem_used_mb / sample.mem_total_mb) if sample.mem_total_mb else 0.0
        for sample in samples
    )
    mem_ratio_series = [
        (sample.mem_used_mb / sample.mem_total_mb) if sample.mem_total_mb else 0.0
        for sample in samples
    ]
    # Follow-up: the longer 12-sample profile can still merge nearby thermal episodes.
    # Keep the first proof run on window_samples=6 and revisit long-window segmentation
    # only after a real trace shows it is worth fixing.
    latest_mem_ratio = mem_ratio_series[-1] if mem_ratio_series else 0.0
    peak_mem_ratio = max(mem_ratio_series or [0.0])
    recent_mem_window = mem_ratio_series[-min(3, len(mem_ratio_series)) :]
    recent_high_mem_samples = sum(1 for ratio in recent_mem_window if ratio >= 0.92)
    high_mem_fraction = _mean(1.0 if ratio >= 0.92 else 0.0 for ratio in mem_ratio_series)
    avg_temp = _mean(sample.temperature_c for sample in samples if sample.temperature_c is not None)
    avg_power_ratio = _mean(
        (sample.power_w / sample.power_limit_w)
        for sample in samples
        if sample.power_w is not None and sample.power_limit_w not in (None, 0)
    )
    throttle_fraction = _mean(
        1.0 if meaningful_throttle_reasons(sample.throttle_reasons) else 0.0
        for sample in samples
    )
    xid_delta = max(0, samples[-1].xid_errors - samples[0].xid_errors)
    ecc_delta = max(0, samples[-1].ecc_errors - samples[0].ecc_errors)
    util_series = [sample.gpu_util / 100.0 for sample in samples]
    util_volatility = statistics.pstdev(util_series) if len(util_series) >= 2 else 0.0
    temp_series = [sample.temperature_c or 0.0 for sample in samples]
    temp_trend = (
        _mean(temp_series[-5:]) - _mean(temp_series[:5])
        if len(temp_series) >= 10
        else (temp_series[-1] - temp_series[0])
    )
    thermal_component = clamp(((avg_temp or 0.0) - 72.0) / 16.0)
    power_component = clamp(((avg_power_ratio or 0.0) - 0.88) / 0.12)
    memory_component = clamp((avg_mem_ratio - 0.84) / 0.16)
    throttle_component = clamp(throttle_fraction)
    error_component = 1.0 if xid_delta > 0 else clamp(ecc_delta / 8.0)
    volatility_component = clamp(util_volatility / 0.22)
    structural_component = clamp(
        max(
            float(structural["hazard"]),
            float(structural["rupture"]),
            clamp(float(structural["entropy"]) / 2.4),
        )
    )
    signal_components = {
        "structural_instability": structural_component,
        "thermal_pressure": thermal_component,
        "power_pressure": power_component,
        "memory_pressure": memory_component,
        "clock_throttle": throttle_component,
        "error_pressure": error_component,
        "utilization_volatility": volatility_component,
    }
    hazard = clamp(
        sum(
            COMPONENT_WEIGHTS[component_name] * component_value
            for component_name, component_value in signal_components.items()
        )
    )
    if xid_delta > 0:
        hazard = max(hazard, 0.95)
    elif throttle_fraction >= 0.5 and (avg_temp or 0.0) >= 80.0:
        hazard = max(hazard, 0.82)
    regime = classify_regime(
        avg_gpu_util=avg_gpu_util,
        avg_mem_ratio=avg_mem_ratio,
        latest_mem_ratio=latest_mem_ratio,
        peak_mem_ratio=peak_mem_ratio,
        recent_high_mem_samples=recent_high_mem_samples,
        high_mem_fraction=high_mem_fraction,
        avg_temp=avg_temp,
        throttle_fraction=throttle_fraction,
        xid_delta=xid_delta,
        ecc_delta=ecc_delta,
        util_volatility=util_volatility,
        coherence=float(structural["coherence"]),
        rupture=float(structural["rupture"]),
        hazard=hazard,
    )
    reasons = describe_regime_reasons(
        regime=regime,
        avg_gpu_util=avg_gpu_util,
        avg_mem_ratio=avg_mem_ratio,
        latest_mem_ratio=latest_mem_ratio,
        recent_high_mem_samples=recent_high_mem_samples,
        avg_temp=avg_temp,
        throttle_fraction=throttle_fraction,
        xid_delta=xid_delta,
        ecc_delta=ecc_delta,
        util_volatility=util_volatility,
        temp_trend=temp_trend,
        coherence=float(structural["coherence"]),
        entropy=float(structural["entropy"]),
        rupture=float(structural["rupture"]),
    )
    confidence, provenance = build_regime_provenance(
        samples=samples,
        regime=regime,
        reasons=reasons,
        scoring_backend=scoring_backend,
        structural=structural,
        signal_components=signal_components,
    )
    return {
        "regime": regime,
        "hazard": round(hazard, 4),
        "scoring_backend": scoring_backend,
        "confidence": round(confidence, 4),
        "signature": str(structural["signature"]),
        "coherence": round(float(structural["coherence"]), 4),
        "stability": round(float(structural["stability"]), 4),
        "entropy": round(float(structural["entropy"]), 4),
        "rupture": round(float(structural["rupture"]), 4),
        "reasons": reasons,
        "provenance": provenance,
        "metrics": {
            "avg_gpu_util": round(avg_gpu_util, 2),
            "avg_mem_util": round(avg_mem_util, 2),
            "avg_mem_ratio": round(avg_mem_ratio, 4),
            "latest_mem_ratio": round(latest_mem_ratio, 4),
            "peak_mem_ratio": round(peak_mem_ratio, 4),
            "recent_high_mem_samples": recent_high_mem_samples,
            "high_mem_fraction": round(high_mem_fraction, 4),
            "avg_temperature_c": round(avg_temp, 2) if avg_temp is not None else None,
            "avg_power_ratio": round(avg_power_ratio, 4) if avg_power_ratio is not None else None,
            "throttle_fraction": round(throttle_fraction, 4),
            "xid_delta": xid_delta,
            "ecc_delta": ecc_delta,
            "util_volatility": round(util_volatility, 4),
            "temp_trend_c": round(temp_trend, 2),
            "avg_pcie_tx": _rounded_mean(sample.pcie_tx for sample in samples if sample.pcie_tx is not None),
            "avg_pcie_rx": _rounded_mean(sample.pcie_rx for sample in samples if sample.pcie_rx is not None),
        },
    }


def encode_samples(samples: Sequence[TelemetrySample]) -> tuple[bytes, Dict[str, Any]]:
    encoded = bytearray()
    severity_series: List[float] = []
    for sample in samples:
        mem_ratio = (sample.mem_used_mb / sample.mem_total_mb) if sample.mem_total_mb else 0.0
        util_bucket = _bucket(sample.gpu_util / 100.0, (0.2, 0.5, 0.8))
        mem_bucket = _bucket(mem_ratio, (0.25, 0.5, 0.82))
        temp_hot = 1 if (sample.temperature_c or 0.0) >= 78.0 else 0
        power_ratio = (
            (sample.power_w / sample.power_limit_w)
            if sample.power_w is not None and sample.power_limit_w not in (None, 0)
            else 0.0
        )
        power_pressure = 1 if power_ratio >= 0.92 else 0
        throttled = 1 if sample.throttle_reasons else 0
        error_flag = 1 if (sample.xid_errors > 0 or sample.ecc_errors > 0) else 0
        byte_value = (
            ((util_bucket & 0b11) << 6)
            | ((mem_bucket & 0b11) << 4)
            | ((temp_hot & 0b1) << 3)
            | ((power_pressure & 0b1) << 2)
            | ((throttled & 0b1) << 1)
            | (error_flag & 0b1)
        )
        encoded.append(byte_value)
        severity_series.append(
            clamp(
                (sample.gpu_util / 100.0) * 0.22
                + mem_ratio * 0.22
                + clamp(((sample.temperature_c or 0.0) - 70.0) / 20.0) * 0.18
                + clamp(power_ratio) * 0.14
                + (0.16 if throttled else 0.0)
                + (0.32 if error_flag else 0.0)
            )
        )
    return bytes(encoded), {"severity_series": severity_series}


def analyze_window(bit_bytes: bytes, severity_series: Sequence[float]) -> Dict[str, Any]:
    try:
        return analyze_window_native(bit_bytes)
    except Exception:
        return analyze_window_fallback(bit_bytes, severity_series)


def analyze_window_native(bit_bytes: bytes) -> Dict[str, Any]:
    try:
        import manifold_engine  # type: ignore

        json_str = manifold_engine.analyze_bytes(bit_bytes, len(bit_bytes), len(bit_bytes), 3)
        parsed = json.loads(json_str)
        window = (parsed.get("windows") or [{}])[0]
        metrics = window.get("metrics") or {}
        return {
            "backend": "native",
            "signature": window.get("signature") or hashlib.sha1(bit_bytes).hexdigest()[:12],
            "coherence": clamp(float(metrics.get("coherence") or 0.0)),
            "stability": clamp(float(metrics.get("stability") or 0.0)),
            "entropy": max(0.0, float(metrics.get("entropy") or 0.0)),
            "rupture": clamp(float(metrics.get("rupture") or 0.0)),
            "hazard": clamp(float(window.get("lambda_hazard") or 0.0)),
        }
    except Exception as exc:  # pragma: no cover - exercised via analyze_window
        raise RuntimeError("native manifold analysis failed") from exc


def analyze_window_fallback(bit_bytes: bytes, severity_series: Sequence[float]) -> Dict[str, Any]:
    transitions = sum(1 for idx in range(1, len(bit_bytes)) if bit_bytes[idx] != bit_bytes[idx - 1])
    coherence = 1.0 - (transitions / max(1, len(bit_bytes) - 1))
    diffs = [abs(severity_series[idx] - severity_series[idx - 1]) for idx in range(1, len(severity_series))]
    stability = 1.0 - min(1.0, (_mean(diffs) / 0.35 if diffs else 0.0))
    entropy = _shannon_entropy(bit_bytes)
    split = max(1, len(severity_series) // 2)
    rupture = clamp(abs(_mean(severity_series[:split]) - _mean(severity_series[split:])) / 0.45)
    hazard = clamp((1.0 - stability) * 0.35 + clamp(entropy / 2.4) * 0.35 + rupture * 0.3)
    return {
        "backend": "fallback",
        "signature": hashlib.sha1(bit_bytes).hexdigest()[:12],
        "coherence": round(coherence, 4),
        "stability": round(stability, 4),
        "entropy": round(entropy, 4),
        "rupture": round(rupture, 4),
        "hazard": round(hazard, 4),
    }


def classify_regime(
    *,
    avg_gpu_util: float,
    avg_mem_ratio: float,
    latest_mem_ratio: float,
    peak_mem_ratio: float,
    recent_high_mem_samples: int,
    high_mem_fraction: float,
    avg_temp: Optional[float],
    throttle_fraction: float,
    xid_delta: int,
    ecc_delta: int,
    util_volatility: float,
    coherence: float,
    rupture: float,
    hazard: float,
) -> str:
    if xid_delta > 0 or ecc_delta >= 8:
        return "error_burst"
    if throttle_fraction >= 0.35 or (avg_temp or 0.0) >= 82.0:
        return "thermal_throttle"
    if avg_mem_ratio >= 0.92:
        return "memory_pressure"
    if latest_mem_ratio >= 0.94 and recent_high_mem_samples >= 3:
        return "memory_pressure"
    if latest_mem_ratio >= 0.92 and recent_high_mem_samples >= 2 and high_mem_fraction >= 0.33:
        return "memory_pressure"
    if peak_mem_ratio >= 0.95 and latest_mem_ratio >= 0.90 and high_mem_fraction >= 0.5:
        return "memory_pressure"
    if util_volatility >= 0.22 or coherence <= 0.35 or rupture >= 0.55:
        return "unstable"
    if avg_gpu_util >= 92.0 and avg_mem_ratio >= 0.65:
        return "saturated"
    if hazard >= 0.58:
        return "degraded"
    if avg_gpu_util >= 45.0 and avg_mem_ratio >= 0.18:
        return "healthy_training"
    return "idle"


def recommended_action(*, regime: str, hazard: float, repetitions: int, reasons: Sequence[str]) -> str:
    instability_evidence = {
        "utilization_whiplash",
        "low_structural_coherence",
        "high_structural_entropy",
    }
    if regime == "error_burst":
        return "quarantine"
    if regime == "thermal_throttle":
        return "throttle"
    if regime == "unstable":
        if hazard >= 0.72:
            return "drain"
        if hazard >= 0.5:
            return "alert"
        if repetitions >= 2 and any(reason in instability_evidence for reason in reasons):
            return "alert"
        return "watch"
    if regime == "degraded" and hazard >= 0.8:
        return "drain"
    if regime == "memory_pressure":
        # Treat full-window high memory on otherwise calm traces as context until the
        # hot tail or other pressure signals indicate an operator-worthy onset.
        if "memory_tail_near_limit" in reasons:
            return "alert"
        if hazard >= 0.28:
            return "alert"
        if "memory_pressure_persistence" in reasons and any(
            reason in {"high_temperature", "temperature_rising", "clock_throttle_detected"}
            for reason in reasons
        ):
            return "alert"
        return "watch"
    if regime == "degraded":
        return "alert"
    if hazard >= 0.5 or any("stale" in reason for reason in reasons):
        return "alert"
    return "watch"


def describe_regime_reasons(
    *,
    regime: str,
    avg_gpu_util: float,
    avg_mem_ratio: float,
    latest_mem_ratio: float,
    recent_high_mem_samples: int,
    avg_temp: Optional[float],
    throttle_fraction: float,
    xid_delta: int,
    ecc_delta: int,
    util_volatility: float,
    temp_trend: float,
    coherence: float,
    entropy: float,
    rupture: float,
) -> List[str]:
    reasons: List[str] = [regime]
    if avg_gpu_util >= 90.0:
        reasons.append("sustained_gpu_saturation")
    if avg_mem_ratio >= 0.9:
        reasons.append("memory_footprint_near_limit")
    if latest_mem_ratio >= 0.94:
        reasons.append("memory_tail_near_limit")
    if recent_high_mem_samples >= 3:
        reasons.append("memory_pressure_persistence")
    if (avg_temp or 0.0) >= 80.0:
        reasons.append("high_temperature")
    if temp_trend >= 8.0:
        reasons.append("temperature_rising")
    if throttle_fraction >= 0.35:
        reasons.append("clock_throttle_detected")
    if xid_delta > 0:
        reasons.append("xid_error_increment")
    if ecc_delta > 0:
        reasons.append("ecc_error_increment")
    if util_volatility >= 0.2:
        reasons.append("utilization_whiplash")
    if coherence <= 0.35:
        reasons.append("low_structural_coherence")
    if entropy >= 1.6:
        reasons.append("high_structural_entropy")
    if rupture >= 0.5:
        reasons.append("regime_rupture")
    return reasons


def build_regime_provenance(
    *,
    samples: Sequence[TelemetrySample],
    regime: str,
    reasons: Sequence[str],
    scoring_backend: str,
    structural: Dict[str, Any],
    signal_components: Dict[str, float],
) -> tuple[float, Dict[str, Any]]:
    optional_signal_coverage = {
        "temperature": _field_coverage(samples, lambda sample: sample.temperature_c is not None),
        "power_draw": _field_coverage(samples, lambda sample: sample.power_w is not None),
        "power_limit": _field_coverage(samples, lambda sample: sample.power_limit_w is not None),
        "pcie_tx": _field_coverage(samples, lambda sample: sample.pcie_tx is not None),
        "pcie_rx": _field_coverage(samples, lambda sample: sample.pcie_rx is not None),
    }
    feature_coverage = _mean(optional_signal_coverage.values())
    collection_sources = sorted({str(sample.collection_source or "unknown") for sample in samples})
    source_quality = _mean(
        COLLECTION_SOURCE_QUALITY.get(source_name, COLLECTION_SOURCE_QUALITY["unknown"])
        for source_name in collection_sources
    )
    backend_quality = 1.0 if scoring_backend == "native" else 0.75
    expected_components = tuple(REGIME_COMPONENT_EXPECTATIONS.get(regime, ()))
    if expected_components:
        component_agreement = _mean(signal_components.get(component_name, 0.0) for component_name in expected_components)
    else:
        component_agreement = clamp(1.0 - (max(signal_components.values(), default=0.0) * 0.25))
    reason_support = clamp(len(reasons) / 3.0)
    signal_agreement = clamp((0.45 * component_agreement) + (0.55 * reason_support))
    confidence = clamp(
        (0.35 * feature_coverage)
        + (0.30 * signal_agreement)
        + (0.20 * source_quality)
        + (0.15 * backend_quality)
    )

    top_factors = []
    for factor_name, factor_score in sorted(
        signal_components.items(),
        key=lambda item: (-(item[1] * COMPONENT_WEIGHTS.get(item[0], 0.0)), -item[1], item[0]),
    ):
        weighted_score = factor_score * COMPONENT_WEIGHTS.get(factor_name, 0.0)
        if weighted_score <= 0.0:
            continue
        top_factors.append(
            {
                "factor": factor_name,
                "label": COMPONENT_LABELS.get(factor_name, factor_name.replace("_", " ")),
                "score": round(factor_score, 4),
                "weight": round(COMPONENT_WEIGHTS.get(factor_name, 0.0), 4),
                "weighted_score": round(weighted_score, 4),
            }
        )

    provenance = {
        "feature_coverage": round(feature_coverage, 4),
        "source_quality": round(source_quality, 4),
        "signal_agreement": round(signal_agreement, 4),
        "backend_quality": round(backend_quality, 4),
        "collection_sources": collection_sources,
        "expected_components": list(expected_components),
        "optional_signal_coverage": {
            field_name: round(coverage, 4) for field_name, coverage in optional_signal_coverage.items()
        },
        "hazard_components": {
            factor_name: round(factor_score, 4) for factor_name, factor_score in signal_components.items()
        },
        "structural_metrics": {
            "coherence": round(float(structural.get("coherence") or 0.0), 4),
            "stability": round(float(structural.get("stability") or 0.0), 4),
            "entropy": round(float(structural.get("entropy") or 0.0), 4),
            "rupture": round(float(structural.get("rupture") or 0.0), 4),
        },
        "top_factors": top_factors[:4],
    }
    return confidence, provenance


def _bucket(value: float, thresholds: Sequence[float]) -> int:
    for index, threshold in enumerate(thresholds):
        if value < threshold:
            return index
    return len(thresholds)


def meaningful_throttle_reasons(reasons: Sequence[str]) -> List[str]:
    return [reason for reason in reasons if reason in MEANINGFUL_THROTTLE_REASONS]


def _field_coverage(samples: Sequence[TelemetrySample], predicate: Any) -> float:
    return _mean(1.0 if predicate(sample) else 0.0 for sample in samples)


def _mean(values: Iterable[Optional[float]]) -> float:
    resolved = [float(value) for value in values if value is not None]
    if not resolved:
        return 0.0
    return float(sum(resolved) / len(resolved))


def _rounded_mean(values: Iterable[float]) -> Optional[float]:
    resolved = [float(value) for value in values if value is not None]
    if not resolved:
        return None
    return round(sum(resolved) / len(resolved), 2)


def _shannon_entropy(values: Sequence[int]) -> float:
    if not values:
        return 0.0
    counts: Dict[int, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    total = float(len(values))
    entropy = 0.0
    for count in counts.values():
        p = count / total
        entropy -= p * math_log2(p)
    return entropy


def math_log2(value: float) -> float:
    import math

    return math.log(value, 2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score telemetry windows into structural regimes")
    parser.add_argument("--redis", default=os.getenv("VALKEY_URL", "redis://localhost:6379/0"))
    parser.add_argument("--window-samples", type=int, default=int(os.getenv("REGIME_WINDOW_SAMPLES", "6")))
    parser.add_argument("--loop-seconds", type=float, default=float(os.getenv("REGIME_LOOP_SECONDS", "5.0")))
    parser.add_argument("--history-retention", type=int, default=int(os.getenv("REGIME_HISTORY_RETENTION", "720")))
    parser.add_argument("--signature-retention-minutes", type=int, default=int(os.getenv("REGIME_SIGNATURE_RETENTION_MINUTES", "180")))
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
    args = build_parser().parse_args()
    cfg = RegimeServiceConfig(
        redis_url=args.redis,
        window_samples=max(6, int(args.window_samples)),
        loop_seconds=max(1.0, float(args.loop_seconds)),
        history_retention=max(60, int(args.history_retention)),
        signature_retention_minutes=max(15, int(args.signature_retention_minutes)),
    )
    service = RegimeService(cfg)

    def _handle_signal(signum: int, _frame: Any) -> None:
        logger.info("received signal %s, stopping regime service", signum)
        service.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    if args.once:
        service.run_once()
        return 0
    service.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
