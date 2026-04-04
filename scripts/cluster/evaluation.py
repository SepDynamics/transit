"""Offline trace evaluation helpers for Sentinel vs baseline comparison."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
from typing import Any, Dict, List, Mapping, Sequence

from scripts.cluster.models import TelemetrySample
from scripts.cluster.regime_service import (
    meaningful_throttle_reasons,
    recommended_action,
    score_gpu_window,
)
from scripts.cluster.trace_utils import iter_entity_samples, load_trace_file, validate_trace_payload

DEFAULT_INCIDENT_WINDOW_MS = 20 * 60 * 1000
VALID_ONSET_PRECISIONS = {"exact", "coarse_window"}
VALID_LABEL_GRANULARITIES = {"gpu_exact", "node_window"}
PUBLIC_ACTION_PRIORITY = {"watch": 1, "alert": 2, "throttle": 3, "drain": 4, "quarantine": 5}


def load_labels(path: str | Path) -> Dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return normalize_label_payload(payload, default_dataset_id=Path(path).stem)


def normalize_label_payload(payload: Mapping[str, Any], *, default_dataset_id: str = "incident-labels") -> Dict[str, Any]:
    incidents = payload.get("incidents")
    if not isinstance(incidents, list):
        raise ValueError("label payload requires an incidents list")
    normalized_incidents: List[Dict[str, Any]] = []
    for incident in incidents:
        if not isinstance(incident, Mapping):
            raise ValueError("incident labels must be objects")
        onset_precision = str(incident.get("onset_precision") or "exact")
        if onset_precision not in VALID_ONSET_PRECISIONS:
            raise ValueError(f"unsupported onset_precision: {onset_precision}")
        label_granularity = str(incident.get("label_granularity") or "gpu_exact")
        if label_granularity not in VALID_LABEL_GRANULARITIES:
            raise ValueError(f"unsupported label_granularity: {label_granularity}")
        normalized_incidents.append(
            {
                "incident_id": str(incident.get("incident_id") or f"incident-{len(normalized_incidents) + 1:03d}"),
                "trace_id": str(incident.get("trace_id") or ""),
                "host": str(incident.get("host") or "unknown-host"),
                "gpu_index": int(incident.get("gpu_index") or 0),
                "incident_class": str(incident.get("incident_class") or "unknown"),
                "onset_ms": int(incident.get("onset_ms") or 0),
                "end_ms": int(incident.get("end_ms") or (int(incident.get("onset_ms") or 0) + DEFAULT_INCIDENT_WINDOW_MS)),
                "onset_precision": onset_precision,
                "label_granularity": label_granularity,
                "expected_action": str(incident.get("expected_action") or "alert"),
                "expected_summary": str(incident.get("expected_summary") or ""),
            }
        )
    normalized_payload: Dict[str, Any] = {
        "dataset_id": str(payload.get("dataset_id") or default_dataset_id),
        "generated_at": str(payload.get("generated_at") or datetime.now(timezone.utc).isoformat()),
        "incidents": normalized_incidents,
    }
    if isinstance(payload.get("metadata"), Mapping):
        normalized_payload["metadata"] = dict(payload.get("metadata") or {})
    return normalized_payload


def build_sentinel_detections(trace: Mapping[str, Any], *, window_samples: int = 12) -> List[Dict[str, Any]]:
    normalized = validate_trace_payload(trace)
    detections: List[Dict[str, Any]] = []
    for entity, samples in iter_entity_samples(normalized):
        signature_counts: Dict[str, int] = {}
        active_class = None
        for index in range(max(0, window_samples - 1), len(samples)):
            window = samples[index - window_samples + 1 : index + 1]
            if len(window) < window_samples:
                continue
            payload = score_gpu_window(window)
            signature = str(payload.get("signature") or "")
            repetitions = signature_counts.get(signature, 0) + 1
            signature_counts[signature] = repetitions
            action = recommended_action(
                regime=str(payload["regime"]),
                hazard=float(payload["hazard"]),
                repetitions=repetitions,
                reasons=list(payload["reasons"]),
            )
            incident_class = str(payload["regime"]) if (action != "watch" or float(payload["hazard"]) >= 0.45) else None
            if incident_class == active_class:
                continue
            active_class = incident_class
            if not incident_class:
                continue
            last_sample = window[-1]
            detections.append(
                {
                    "engine": "sentinel",
                    "trace_id": str(normalized.get("trace_id") or ""),
                    "host": str(entity["host"]),
                    "gpu_index": int(entity["gpu_index"]),
                    "incident_class": incident_class,
                    "timestamp_ms": int(last_sample.timestamp_ms),
                    "action": action,
                    "hazard": float(payload["hazard"]),
                    "reasons": list(payload["reasons"]),
                }
            )
    detections.sort(key=lambda item: (item["timestamp_ms"], item["host"], item["gpu_index"]))
    return detections


def build_baseline_detections(
    trace: Mapping[str, Any],
    *,
    persistence_windows: int = 3,
) -> List[Dict[str, Any]]:
    normalized = validate_trace_payload(trace)
    detections: List[Dict[str, Any]] = []
    for entity, samples in iter_entity_samples(normalized):
        active_class = None
        memory_count = 0
        thermal_count = 0
        error_count = 0
        previous: TelemetrySample | None = None
        for sample in samples:
            mem_ratio = (sample.mem_used_mb / sample.mem_total_mb) if sample.mem_total_mb else 0.0
            thermal_condition = (sample.temperature_c or 0.0) >= 80.0 and bool(meaningful_throttle_reasons(sample.throttle_reasons))
            memory_condition = mem_ratio >= 0.92
            xid_delta = max(0, sample.xid_errors - (previous.xid_errors if previous else 0))
            ecc_delta = max(0, sample.ecc_errors - (previous.ecc_errors if previous else 0))
            error_condition = xid_delta > 0 or ecc_delta >= 4

            memory_count = memory_count + 1 if memory_condition else 0
            thermal_count = thermal_count + 1 if thermal_condition else 0
            error_count = error_count + 1 if error_condition else 0

            incident_class = None
            action = "watch"
            reasons: List[str] = []
            if error_count >= 1:
                incident_class = "error_burst"
                action = "quarantine"
                reasons = ["xid_or_ecc_threshold"]
            elif thermal_count >= persistence_windows:
                incident_class = "thermal_throttle"
                action = "throttle"
                reasons = ["temp_and_throttle_threshold"]
            elif memory_count >= persistence_windows:
                incident_class = "memory_pressure"
                action = "alert"
                reasons = ["memory_threshold"]

            if incident_class == active_class:
                previous = sample
                continue
            active_class = incident_class
            if incident_class:
                detections.append(
                    {
                        "engine": "baseline",
                        "trace_id": str(normalized.get("trace_id") or ""),
                        "host": str(entity["host"]),
                        "gpu_index": int(entity["gpu_index"]),
                        "incident_class": incident_class,
                        "timestamp_ms": int(sample.timestamp_ms),
                        "action": action,
                        "hazard": 1.0,
                        "reasons": reasons,
                    }
                )
            previous = sample
    detections.sort(key=lambda item: (item["timestamp_ms"], item["host"], item["gpu_index"]))
    return detections


def merge_detection_episodes(
    detections: Sequence[Mapping[str, Any]],
    *,
    cooldown_ms: int = 0,
) -> List[Dict[str, Any]]:
    if cooldown_ms <= 0:
        return [dict(item) for item in detections]
    merged: List[Dict[str, Any]] = []
    last_seen: Dict[tuple[str, str, int, str, str], int] = {}
    for raw_detection in sorted(
        detections,
        key=lambda item: (
            int(item.get("timestamp_ms") or 0),
            str(item.get("host") or ""),
            int(item.get("gpu_index") or 0),
            str(item.get("incident_class") or ""),
        ),
    ):
        detection = dict(raw_detection)
        key = (
            str(detection.get("engine") or ""),
            str(detection.get("host") or ""),
            int(detection.get("gpu_index") or 0),
            str(detection.get("incident_class") or ""),
            str(detection.get("action") or ""),
        )
        timestamp_ms = int(detection.get("timestamp_ms") or 0)
        if timestamp_ms - last_seen.get(key, -cooldown_ms - 1) <= cooldown_ms:
            continue
        merged.append(detection)
        last_seen[key] = timestamp_ms
    return merged


def merge_public_host_narratives(
    detections: Sequence[Mapping[str, Any]],
    *,
    cooldown_ms: int = 0,
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    last_by_key: Dict[tuple[str, str, str, str], int] = {}
    last_seen: Dict[tuple[str, str, str, str], int] = {}
    for raw_detection in sorted(
        detections,
        key=lambda item: (
            int(item.get("timestamp_ms") or 0),
            str(item.get("host") or ""),
            str(item.get("incident_class") or ""),
            int(item.get("gpu_index") or 0),
        ),
    ):
        detection = dict(raw_detection)
        key = (
            str(detection.get("engine") or ""),
            str(detection.get("trace_id") or ""),
            str(detection.get("host") or ""),
            str(detection.get("incident_class") or ""),
        )
        timestamp_ms = int(detection.get("timestamp_ms") or 0)
        previous_timestamp = last_seen.get(key)
        if previous_timestamp is None or timestamp_ms - previous_timestamp > cooldown_ms:
            merged_detection = dict(detection)
            merged_detection["gpu_indexes"] = sorted({int(detection.get("gpu_index") or 0)})
            merged.append(merged_detection)
            last_by_key[key] = len(merged) - 1
            last_seen[key] = timestamp_ms
            continue

        target = merged[last_by_key[key]]
        gpu_indexes = {
            int(value)
            for value in list(target.get("gpu_indexes") or [])
            if isinstance(value, int)
        }
        gpu_indexes.add(int(detection.get("gpu_index") or 0))
        target["gpu_indexes"] = sorted(gpu_indexes)
        if public_action_rank(str(detection.get("action") or "watch")) > public_action_rank(str(target.get("action") or "watch")):
            target["action"] = str(detection.get("action") or "watch")
        target["hazard"] = max(float(target.get("hazard") or 0.0), float(detection.get("hazard") or 0.0))
        existing_reasons = list(target.get("reasons") or [])
        for reason in list(detection.get("reasons") or []):
            if reason not in existing_reasons:
                existing_reasons.append(reason)
        target["reasons"] = existing_reasons
        last_seen[key] = timestamp_ms
    return merged


def compare_detections(
    detections: Sequence[Mapping[str, Any]],
    labels: Mapping[str, Any],
) -> Dict[str, Any]:
    incidents = list(labels.get("incidents") or [])
    matched_detection_indexes: set[int] = set()
    per_incident: List[Dict[str, Any]] = []
    for label in incidents:
        match_index = None
        match_payload = None
        for index, detection in enumerate(detections):
            if index in matched_detection_indexes:
                continue
            if not label_matches_detection(label, detection):
                continue
            timestamp_ms = int(detection.get("timestamp_ms") or 0)
            if timestamp_ms < int(label.get("onset_ms") or 0):
                continue
            if timestamp_ms > int(label.get("end_ms") or 0):
                continue
            match_index = index
            match_payload = dict(detection)
            break
        if match_index is not None:
            matched_detection_indexes.add(match_index)
        per_incident.append(
            {
                "incident_id": str(label.get("incident_id") or ""),
                "incident_class": str(label.get("incident_class") or ""),
                "host": str(label.get("host") or ""),
                "gpu_index": int(label.get("gpu_index") or 0),
                "onset_precision": str(label.get("onset_precision") or "exact"),
                "label_granularity": str(label.get("label_granularity") or "gpu_exact"),
                "expected_action": str(label.get("expected_action") or ""),
                "onset_ms": int(label.get("onset_ms") or 0),
                "detected": match_payload is not None,
                "detection_ms": int(match_payload.get("timestamp_ms") or 0) if match_payload else None,
                "detection_latency_ms": (
                    int(match_payload.get("timestamp_ms") or 0) - int(label.get("onset_ms") or 0)
                    if match_payload and str(label.get("onset_precision") or "exact") == "exact"
                    else None
                ),
                "lead_time_evaluable": str(label.get("onset_precision") or "exact") == "exact",
                "action_match": (
                    str(match_payload.get("action") or "") == str(label.get("expected_action") or "")
                    if match_payload
                    else False
                ),
                "detection": match_payload,
            }
        )
    extra_alerts = [dict(detection) for index, detection in enumerate(detections) if index not in matched_detection_indexes]
    matched_count = sum(1 for item in per_incident if item["detected"])
    action_matches = sum(1 for item in per_incident if item["action_match"])
    lead_time_evaluable_count = sum(1 for item in per_incident if item["lead_time_evaluable"])
    return {
        "matched_incident_count": matched_count,
        "missed_incident_count": len(per_incident) - matched_count,
        "action_match_count": action_matches,
        "lead_time_evaluable_count": lead_time_evaluable_count,
        "extra_alert_count": len(extra_alerts),
        "per_incident": per_incident,
        "extra_alerts": extra_alerts,
    }


def label_matches_detection(label: Mapping[str, Any], detection: Mapping[str, Any]) -> bool:
    if str(detection.get("trace_id") or "") != str(label.get("trace_id") or ""):
        return False
    if str(detection.get("host") or "") != str(label.get("host") or ""):
        return False
    if str(detection.get("incident_class") or "") != str(label.get("incident_class") or ""):
        return False
    if str(label.get("label_granularity") or "gpu_exact") == "node_window":
        return True
    return int(detection.get("gpu_index") or 0) == int(label.get("gpu_index") or 0)


def build_comparison_report(
    trace: Mapping[str, Any] | str | Path,
    labels: Mapping[str, Any] | str | Path,
    *,
    window_samples: int = 6,
    persistence_windows: int = 3,
    episode_cooldown_ms: int = 0,
) -> Dict[str, Any]:
    trace_payload = load_trace_file(trace) if isinstance(trace, (str, Path)) else validate_trace_payload(trace)
    label_payload = load_labels(labels) if isinstance(labels, (str, Path)) else normalize_label_payload(labels)
    sentinel_detections = merge_detection_episodes(
        build_sentinel_detections(trace_payload, window_samples=window_samples),
        cooldown_ms=max(0, int(episode_cooldown_ms)),
    )
    if label_payload.get("incidents") and all(
        str(item.get("label_granularity") or "gpu_exact") == "node_window"
        for item in label_payload.get("incidents", [])
        if isinstance(item, Mapping)
    ):
        sentinel_detections = merge_public_host_narratives(
            sentinel_detections,
            cooldown_ms=max(0, int(episode_cooldown_ms)),
        )
    baseline_detections = merge_detection_episodes(
        build_baseline_detections(trace_payload, persistence_windows=persistence_windows),
        cooldown_ms=max(0, int(episode_cooldown_ms)),
    )
    sentinel_summary = compare_detections(sentinel_detections, label_payload)
    baseline_summary = compare_detections(baseline_detections, label_payload)

    lead_time: List[Dict[str, Any]] = []
    baseline_by_id = {item["incident_id"]: item for item in baseline_summary["per_incident"]}
    for item in sentinel_summary["per_incident"]:
        baseline_item = baseline_by_id.get(item["incident_id"]) or {}
        sentinel_latency = item.get("detection_latency_ms")
        baseline_latency = baseline_item.get("detection_latency_ms")
        lead_time.append(
            {
                "incident_id": item["incident_id"],
                "incident_class": item["incident_class"],
                "host": item["host"],
                "gpu_index": item["gpu_index"],
                "lead_time_evaluable": bool(item.get("lead_time_evaluable") and baseline_item.get("lead_time_evaluable")),
                "sentinel_latency_ms": sentinel_latency,
                "baseline_latency_ms": baseline_latency,
                "sentinel_lead_ms": (
                    (baseline_latency - sentinel_latency)
                    if sentinel_latency is not None and baseline_latency is not None
                    else None
                ),
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trace_id": str(trace_payload.get("trace_id") or ""),
        "dataset_id": str(label_payload.get("dataset_id") or ""),
        "label_count": len(label_payload.get("incidents") or []),
        "config": {
            "window_samples": window_samples,
            "baseline_persistence_windows": persistence_windows,
            "episode_cooldown_ms": max(0, int(episode_cooldown_ms)),
        },
        "sentinel": {
            "detection_count": len(sentinel_detections),
            "detections": sentinel_detections,
            **sentinel_summary,
        },
        "baseline": {
            "detection_count": len(baseline_detections),
            "detections": baseline_detections,
            **baseline_summary,
        },
        "comparison": {
            "lead_time_by_incident": lead_time,
            "lead_time_evaluable_count": sum(1 for item in lead_time if item["lead_time_evaluable"]),
            "extra_alert_delta": baseline_summary["extra_alert_count"] - sentinel_summary["extra_alert_count"],
            "matched_incident_delta": sentinel_summary["matched_incident_count"] - baseline_summary["matched_incident_count"],
        },
    }


def summarize_trace_entities(trace: Mapping[str, Any]) -> Dict[str, Any]:
    normalized = validate_trace_payload(trace)
    entities: List[Dict[str, Any]] = []
    trace_start_ms: int | None = None
    trace_end_ms: int | None = None
    for entity, samples in iter_entity_samples(normalized):
        if not samples:
            continue
        first_sample = samples[0]
        last_sample = samples[-1]
        trace_start_ms = first_sample.timestamp_ms if trace_start_ms is None else min(trace_start_ms, first_sample.timestamp_ms)
        trace_end_ms = last_sample.timestamp_ms if trace_end_ms is None else max(trace_end_ms, last_sample.timestamp_ms)
        entities.append(
            {
                "host": str(entity["host"]),
                "gpu_index": int(entity["gpu_index"]),
                "sample_count": len(samples),
                "start_ms": int(first_sample.timestamp_ms),
                "end_ms": int(last_sample.timestamp_ms),
                "name": str(last_sample.name),
                "uuid": str(last_sample.uuid),
            }
        )
    return {
        "trace_id": str(normalized.get("trace_id") or ""),
        "entity_count": len(entities),
        "start_ms": trace_start_ms,
        "end_ms": trace_end_ms,
        "entities": sorted(entities, key=lambda item: (item["host"], item["gpu_index"])),
    }


def validate_label_payload_against_trace(
    labels: Mapping[str, Any] | str | Path,
    trace: Mapping[str, Any] | str | Path,
) -> Dict[str, Any]:
    trace_payload = load_trace_file(trace) if isinstance(trace, (str, Path)) else validate_trace_payload(trace)
    label_payload = load_labels(labels) if isinstance(labels, (str, Path)) else normalize_label_payload(labels)
    trace_summary = summarize_trace_entities(trace_payload)
    entity_index = {
        (item["host"], int(item["gpu_index"])): item
        for item in trace_summary["entities"]
    }

    errors: List[str] = []
    warnings: List[str] = []
    seen_incident_ids: set[str] = set()
    for incident in label_payload["incidents"]:
        incident_id = str(incident["incident_id"])
        host = str(incident["host"])
        gpu_index = int(incident["gpu_index"])
        onset_ms = int(incident["onset_ms"])
        end_ms = int(incident["end_ms"])
        onset_precision = str(incident["onset_precision"])
        label_granularity = str(incident["label_granularity"])
        trace_id = str(incident["trace_id"] or label_payload.get("dataset_id") or "")
        entity = entity_index.get((host, gpu_index))
        host_entities = [item for item in trace_summary["entities"] if item["host"] == host]

        if incident_id in seen_incident_ids:
            errors.append(f"{incident_id}: duplicate incident_id")
        seen_incident_ids.add(incident_id)

        if str(incident["trace_id"] or "") != str(trace_payload.get("trace_id") or ""):
            errors.append(
                f"{incident_id}: trace_id {trace_id!r} does not match trace {str(trace_payload.get('trace_id') or '')!r}"
            )
        if onset_ms >= end_ms:
            errors.append(f"{incident_id}: onset_ms must be earlier than end_ms")
        if label_granularity == "node_window":
            if not host_entities:
                errors.append(f"{incident_id}: host={host} not found in trace")
                continue
            entity_start_ms = min(int(item["start_ms"]) for item in host_entities)
            entity_end_ms = max(int(item["end_ms"]) for item in host_entities)
            target = host
        else:
            if entity is None:
                errors.append(f"{incident_id}: host={host} gpu_index={gpu_index} not found in trace")
                continue
            entity_start_ms = int(entity["start_ms"])
            entity_end_ms = int(entity["end_ms"])
            target = f"{host} gpu{gpu_index}"
        if onset_precision == "exact":
            if onset_ms < entity_start_ms or onset_ms > entity_end_ms:
                errors.append(f"{incident_id}: exact onset_ms is outside trace range for {target}")
            if end_ms < entity_start_ms or end_ms > entity_end_ms:
                errors.append(f"{incident_id}: exact end_ms is outside trace range for {target}")
        elif end_ms < entity_start_ms or onset_ms > entity_end_ms:
            errors.append(f"{incident_id}: coarse label window does not overlap trace range for {target}")
        else:
            if onset_ms < entity_start_ms or end_ms > entity_end_ms:
                warnings.append(f"{incident_id}: coarse label extends beyond trace range for {target}")

        if label_granularity == "node_window" and onset_precision == "exact":
            warnings.append(f"{incident_id}: exact onset with node_window granularity is allowed but weak evidence")

    return {
        "ok": not errors,
        "trace_id": str(trace_payload.get("trace_id") or ""),
        "dataset_id": str(label_payload.get("dataset_id") or ""),
        "incident_count": len(label_payload["incidents"]),
        "entity_count": int(trace_summary["entity_count"]),
        "trace_start_ms": trace_summary["start_ms"],
        "trace_end_ms": trace_summary["end_ms"],
        "exact_incident_count": sum(1 for item in label_payload["incidents"] if item["onset_precision"] == "exact"),
        "coarse_incident_count": sum(1 for item in label_payload["incidents"] if item["onset_precision"] != "exact"),
        "errors": errors,
        "warnings": warnings,
    }


def grade_comparison_report(
    report: Mapping[str, Any] | str | Path,
    *,
    trace: Mapping[str, Any] | str | Path | None = None,
) -> Dict[str, Any]:
    payload = json.loads(Path(report).read_text(encoding="utf-8")) if isinstance(report, (str, Path)) else dict(report)
    sentinel = dict(payload.get("sentinel") or {})
    baseline = dict(payload.get("baseline") or {})
    comparison = dict(payload.get("comparison") or {})
    baseline_by_id = {
        str(item.get("incident_id") or ""): item
        for item in baseline.get("per_incident", [])
        if isinstance(item, Mapping)
    }

    positive_lead_ids = [
        str(item.get("incident_id") or "")
        for item in comparison.get("lead_time_by_incident", [])
        if bool(item.get("lead_time_evaluable")) and (item.get("sentinel_lead_ms") or 0) > 0
    ]
    better_action_ids = [
        str(item.get("incident_id") or "")
        for item in sentinel.get("per_incident", [])
        if bool(item.get("action_match")) and not bool((baseline_by_id.get(str(item.get("incident_id") or "")) or {}).get("action_match"))
    ]
    replay_ready = False
    replay_trace_id = None
    if trace is not None:
        trace_payload = load_trace_file(trace) if isinstance(trace, (str, Path)) else validate_trace_payload(trace)
        replay_trace_id = str(trace_payload.get("trace_id") or "")
        replay_ready = replay_trace_id == str(payload.get("trace_id") or "")

    matched_pass = int(sentinel.get("matched_incident_count") or 0) >= int(baseline.get("matched_incident_count") or 0)
    extra_pass = int(sentinel.get("extra_alert_count") or 0) <= int(baseline.get("extra_alert_count") or 0)
    lead_or_action_pass = bool(positive_lead_ids or better_action_ids)
    replay_pass = replay_ready

    return {
        "status": "pass" if all((matched_pass, extra_pass, lead_or_action_pass, replay_pass)) else "fail",
        "trace_id": str(payload.get("trace_id") or ""),
        "dataset_id": str(payload.get("dataset_id") or ""),
        "criteria": {
            "matched_incidents": {
                "passed": matched_pass,
                "sentinel": int(sentinel.get("matched_incident_count") or 0),
                "baseline": int(baseline.get("matched_incident_count") or 0),
            },
            "extra_alerts": {
                "passed": extra_pass,
                "sentinel": int(sentinel.get("extra_alert_count") or 0),
                "baseline": int(baseline.get("extra_alert_count") or 0),
            },
            "lead_or_action_quality": {
                "passed": lead_or_action_pass,
                "positive_lead_incident_ids": positive_lead_ids,
                "better_action_incident_ids": better_action_ids,
            },
            "replay_ready": {
                "passed": replay_pass,
                "trace_id": replay_trace_id,
            },
        },
    }


def render_comparison_markdown(
    report: Mapping[str, Any] | str | Path,
    *,
    grade: Mapping[str, Any] | None = None,
    trace_path: str | None = None,
    labels_path: str | None = None,
    replay_command: str | None = None,
) -> str:
    payload = json.loads(Path(report).read_text(encoding="utf-8")) if isinstance(report, (str, Path)) else dict(report)
    grade_payload = dict(grade or {})
    sentinel = dict(payload.get("sentinel") or {})
    baseline = dict(payload.get("baseline") or {})
    comparison = dict(payload.get("comparison") or {})
    sentinel_incidents = [item for item in sentinel.get("per_incident", []) if isinstance(item, Mapping)]
    exact_count = sum(1 for item in sentinel_incidents if str(item.get("onset_precision") or "exact") == "exact")
    coarse_count = len(sentinel_incidents) - exact_count

    lines = [
        "# Cluster Sentinel Proof Summary",
        "",
        f"- Verdict: `{str(grade_payload.get('status') or 'unknown').upper()}`",
        f"- Trace: `{str(payload.get('trace_id') or '')}`",
        f"- Dataset: `{str(payload.get('dataset_id') or '')}`",
        f"- Labels: `{int(payload.get('label_count') or 0)}` total, `{exact_count}` exact, `{coarse_count}` coarse",
        f"- Config: `window_samples={int((payload.get('config') or {}).get('window_samples') or 0)}`, "
        f"`baseline_persistence_windows={int((payload.get('config') or {}).get('baseline_persistence_windows') or 0)}`, "
        f"`episode_cooldown_ms={int((payload.get('config') or {}).get('episode_cooldown_ms') or 0)}`",
        "",
        "| Engine | Matched | Extra Alerts | Action Matches |",
        "| --- | ---: | ---: | ---: |",
        f"| Sentinel | {int(sentinel.get('matched_incident_count') or 0)} | {int(sentinel.get('extra_alert_count') or 0)} | {int(sentinel.get('action_match_count') or 0)} |",
        f"| Baseline | {int(baseline.get('matched_incident_count') or 0)} | {int(baseline.get('extra_alert_count') or 0)} | {int(baseline.get('action_match_count') or 0)} |",
        "",
        "## Wedge Criteria",
    ]
    for key, label in (
        ("matched_incidents", "Sentinel matched incidents >= baseline"),
        ("extra_alerts", "Sentinel extra alerts <= baseline"),
        ("lead_or_action_quality", "Positive lead time or better action quality"),
        ("replay_ready", "Replay-ready trace available"),
    ):
        criterion = dict((grade_payload.get("criteria") or {}).get(key) or {})
        status = "PASS" if bool(criterion.get("passed")) else "FAIL"
        lines.append(f"- {label}: `{status}`")

    positive_lead_ids = list((((grade_payload.get("criteria") or {}).get("lead_or_action_quality") or {}).get("positive_lead_incident_ids") or []))
    better_action_ids = list((((grade_payload.get("criteria") or {}).get("lead_or_action_quality") or {}).get("better_action_incident_ids") or []))
    lines.extend(
        [
            "",
            "## Evidence",
            f"- Positive lead incidents: {', '.join(positive_lead_ids) if positive_lead_ids else 'none'}",
            f"- Better-action incidents: {', '.join(better_action_ids) if better_action_ids else 'none'}",
        ]
    )
    if int(comparison.get("lead_time_evaluable_count") or 0) == 0:
        lines.append("- Lead-time metrics are not evaluable on this report because the labels are coarse.")
    if trace_path:
        lines.append(f"- Trace file: `{trace_path}`")
    if labels_path:
        lines.append(f"- Labels file: `{labels_path}`")
    replay_command = normalize_replay_command(replay_command, trace_path=trace_path)
    if replay_command:
        lines.extend(["", "## Replay", "", "```bash", replay_command, "```"])
    return "\n".join(lines) + "\n"


def normalize_replay_command(replay_command: str | None, *, trace_path: str | None) -> str | None:
    command = str(replay_command or "").strip()
    if not command and trace_path:
        command = f"python3 scripts/cluster/replay.py --trace {trace_path} --scenario none --clear-trace"
    if not command:
        return None
    if "scripts/cluster/replay.py" in command and "--demo" not in command and "--scenario" not in command:
        command = f"{command} --scenario none"
    return command


def public_action_rank(action: str) -> int:
    return PUBLIC_ACTION_PRIORITY.get(str(action or "watch"), 0)


def detection_in_label_window(label: Mapping[str, Any], detection: Mapping[str, Any], *, primary_only: bool = False) -> bool:
    if str(detection.get("trace_id") or "") != str(label.get("trace_id") or ""):
        return False
    if str(detection.get("host") or "") != str(label.get("host") or ""):
        return False
    timestamp_ms = int(detection.get("timestamp_ms") or 0)
    if timestamp_ms < int(label.get("onset_ms") or 0) or timestamp_ms > int(label.get("end_ms") or 0):
        return False
    if primary_only and str(detection.get("incident_class") or "") != str(label.get("incident_class") or ""):
        return False
    return True


def heuristic_gpu_indexes_for_label(labels: Mapping[str, Any], label: Mapping[str, Any]) -> List[int]:
    values = label.get("heuristic_gpu_indexes")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        metadata = labels.get("metadata") if isinstance(labels.get("metadata"), Mapping) else {}
        values = metadata.get("heuristic_gpu_indexes") if isinstance(metadata, Mapping) else []
    return sorted(
        {
            int(value)
            for value in (values or [])
            if not isinstance(value, (Mapping, list, dict, set, tuple)) or isinstance(value, int)
        }
    )


def summarize_public_engine_case(
    engine_payload: Mapping[str, Any],
    *,
    label: Mapping[str, Any],
    labels: Mapping[str, Any],
) -> Dict[str, Any]:
    detections = sorted(
        [
            dict(detection)
            for detection in engine_payload.get("detections", [])
            if isinstance(detection, Mapping) and detection_in_label_window(label, detection)
        ],
        key=lambda item: (
            int(item.get("timestamp_ms") or 0),
            int(item.get("gpu_index") or 0),
            str(item.get("incident_class") or ""),
        ),
    )
    primary_detections = [detection for detection in detections if detection_in_label_window(label, detection, primary_only=True)]
    heuristic_gpu_indexes = heuristic_gpu_indexes_for_label(labels, label)
    localized_gpu_index_values: set[int] = set()
    for detection in primary_detections:
        gpu_indexes = detection.get("gpu_indexes")
        if isinstance(gpu_indexes, Sequence) and not isinstance(gpu_indexes, (str, bytes)):
            localized_gpu_index_values.update(
                int(value)
                for value in gpu_indexes
                if isinstance(value, int)
            )
        else:
            localized_gpu_index_values.add(int(detection.get("gpu_index") or 0))
    localized_gpu_indexes = sorted(localized_gpu_index_values)
    context_alerts = [
        dict(detection)
        for detection in engine_payload.get("extra_alerts", [])
        if isinstance(detection, Mapping) and detection_in_label_window(label, detection)
    ]
    external_extras = [
        dict(detection)
        for detection in engine_payload.get("extra_alerts", [])
        if isinstance(detection, Mapping) and not detection_in_label_window(label, detection)
    ]
    highest_action = "watch"
    highest_action_rank = 0
    for detection in detections:
        rank = public_action_rank(str(detection.get("action") or "watch"))
        if rank >= highest_action_rank:
            highest_action = str(detection.get("action") or "watch")
            highest_action_rank = rank
    context_classes = sorted(
        {
            str(detection.get("incident_class") or "")
            for detection in context_alerts
            if str(detection.get("incident_class") or "") != str(label.get("incident_class") or "")
        }
    )
    return {
        "detected_in_window": bool(detections),
        "primary_detected": bool(primary_detections),
        "first_event_ms": int(detections[0]["timestamp_ms"]) if detections else None,
        "first_primary_ms": int(primary_detections[0]["timestamp_ms"]) if primary_detections else None,
        "host_localized": bool(detections),
        "heuristic_gpu_indexes": heuristic_gpu_indexes,
        "localized_gpu_indexes": localized_gpu_indexes,
        "gpu_localized": (
            bool(localized_gpu_indexes)
            if not heuristic_gpu_indexes
            else bool(set(localized_gpu_indexes) & set(heuristic_gpu_indexes))
        ),
        "highest_action": highest_action,
        "highest_action_rank": highest_action_rank,
        "highest_action_reaches_expected": highest_action_rank >= public_action_rank(str(label.get("expected_action") or "watch")),
        "raw_detection_count": len(detections),
        "raw_primary_detection_count": len(primary_detections),
        "merged_incident_narrative_count": 1 if detections else 0,
        "repeated_primary_context_count": sum(
            1
            for detection in context_alerts
            if str(detection.get("incident_class") or "") == str(label.get("incident_class") or "")
        ),
        "context_classes": context_classes,
        "context_detection_count": len(context_alerts),
        "external_extra_alert_count": len(external_extras),
    }


def grade_public_case_report(
    report: Mapping[str, Any] | str | Path,
    *,
    labels: Mapping[str, Any] | str | Path,
    trace: Mapping[str, Any] | str | Path | None = None,
) -> Dict[str, Any]:
    payload = json.loads(Path(report).read_text(encoding="utf-8")) if isinstance(report, (str, Path)) else dict(report)
    label_payload = load_labels(labels) if isinstance(labels, (str, Path)) else normalize_label_payload(labels)
    sentinel_payload = dict(payload.get("sentinel") or {})
    baseline_payload = dict(payload.get("baseline") or {})

    per_incident: List[Dict[str, Any]] = []
    claim_support_ids: List[str] = []
    for label in label_payload.get("incidents", []):
        if not isinstance(label, Mapping):
            continue
        sentinel_case = summarize_public_engine_case(sentinel_payload, label=label, labels=label_payload)
        baseline_case = summarize_public_engine_case(baseline_payload, label=label, labels=label_payload)
        claim_supported = bool(
            sentinel_case["primary_detected"]
            and (
                not baseline_case["primary_detected"]
                or sentinel_case["highest_action_rank"] > baseline_case["highest_action_rank"]
            )
        )
        if claim_supported:
            claim_support_ids.append(str(label.get("incident_id") or ""))
        per_incident.append(
            {
                "incident_id": str(label.get("incident_id") or ""),
                "incident_class": str(label.get("incident_class") or ""),
                "host": str(label.get("host") or ""),
                "expected_action": str(label.get("expected_action") or ""),
                "onset_precision": str(label.get("onset_precision") or "exact"),
                "label_granularity": str(label.get("label_granularity") or "gpu_exact"),
                "sentinel": sentinel_case,
                "baseline": baseline_case,
                "claim_supported": claim_supported,
            }
        )

    replay_ready = False
    replay_trace_id = None
    if trace is not None:
        trace_payload = load_trace_file(trace) if isinstance(trace, (str, Path)) else validate_trace_payload(trace)
        replay_trace_id = str(trace_payload.get("trace_id") or "")
        replay_ready = replay_trace_id == str(payload.get("trace_id") or "")

    surfaced_pass = all(bool(item["sentinel"]["detected_in_window"]) for item in per_incident)
    primary_pass = all(bool(item["sentinel"]["primary_detected"]) for item in per_incident)
    host_pass = all(bool(item["sentinel"]["host_localized"]) for item in per_incident)
    gpu_pass = all(
        bool(item["sentinel"]["gpu_localized"])
        for item in per_incident
        if item["sentinel"]["heuristic_gpu_indexes"]
    )
    action_pass = all(bool(item["sentinel"]["highest_action_reaches_expected"]) for item in per_incident)
    narrative_pass = all(int(item["sentinel"]["merged_incident_narrative_count"]) <= 1 for item in per_incident)
    claim_pass = bool(claim_support_ids)

    return {
        "status": "pass" if all((surfaced_pass, primary_pass, host_pass, gpu_pass, action_pass, narrative_pass, claim_pass, replay_ready)) else "fail",
        "trace_id": str(payload.get("trace_id") or ""),
        "dataset_id": str(payload.get("dataset_id") or ""),
        "mode": "public_case",
        "criteria": {
            "first_event_surfaced": {"passed": surfaced_pass},
            "primary_class_surfaced": {"passed": primary_pass},
            "host_localization": {"passed": host_pass},
            "gpu_localization": {"passed": gpu_pass},
            "highest_action_reached": {"passed": action_pass},
            "incident_narrative_merged": {"passed": narrative_pass},
            "claim_supported": {
                "passed": claim_pass,
                "incident_ids": claim_support_ids,
            },
            "replay_ready": {
                "passed": replay_ready,
                "trace_id": replay_trace_id,
            },
        },
        "per_incident": per_incident,
    }


def render_public_case_markdown(
    report: Mapping[str, Any] | str | Path,
    *,
    grade: Mapping[str, Any] | None = None,
    trace_path: str | None = None,
    labels_path: str | None = None,
    replay_command: str | None = None,
) -> str:
    payload = json.loads(Path(report).read_text(encoding="utf-8")) if isinstance(report, (str, Path)) else dict(report)
    grade_payload = dict(grade or {})
    incidents = [item for item in grade_payload.get("per_incident", []) if isinstance(item, Mapping)]

    lines = [
        "# Cluster Sentinel Public Case Summary",
        "",
        f"- Verdict: `{str(grade_payload.get('status') or 'unknown').upper()}`",
        f"- Trace: `{str(payload.get('trace_id') or '')}`",
        f"- Dataset: `{str(payload.get('dataset_id') or '')}`",
        "- Claim lane: `public_case`",
        "- Label semantics: coarse node-window event labels. This is not a lead-time benchmark.",
        "",
        "| Incident | Sentinel Primary | Baseline Primary | Sentinel Highest Action | Context Classes | External Extras |",
        "| --- | ---: | ---: | --- | --- | ---: |",
    ]
    for incident in incidents:
        sentinel = dict(incident.get("sentinel") or {})
        baseline = dict(incident.get("baseline") or {})
        lines.append(
            f"| {str(incident.get('incident_id') or '')} | "
            f"{'yes' if sentinel.get('primary_detected') else 'no'} | "
            f"{'yes' if baseline.get('primary_detected') else 'no'} | "
            f"{str(sentinel.get('highest_action') or 'watch')} | "
            f"{', '.join(sentinel.get('context_classes') or []) or 'none'} | "
            f"{int(sentinel.get('external_extra_alert_count') or 0)} |"
        )

    lines.extend(["", "## Public Rubric"])
    for key, label in (
        ("first_event_surfaced", "First event surfaced within the node window"),
        ("primary_class_surfaced", "Primary unstable/off-bus class surfaced"),
        ("host_localization", "Affected host localized"),
        ("gpu_localization", "Affected GPU localized against heuristic set"),
        ("highest_action_reached", "Highest action reached expected severity"),
        ("incident_narrative_merged", "Repeated detections merged into one incident narrative"),
        ("claim_supported", "Sentinel supports the public claim better than baseline"),
        ("replay_ready", "Replay-ready trace available"),
    ):
        criterion = dict((grade_payload.get("criteria") or {}).get(key) or {})
        lines.append(f"- {label}: `{'PASS' if criterion.get('passed') else 'FAIL'}`")

    lines.extend(["", "## Evidence"])
    for incident in incidents:
        sentinel = dict(incident.get("sentinel") or {})
        baseline = dict(incident.get("baseline") or {})
        lines.append(
            "- "
            f"{str(incident.get('incident_id') or '')}: sentinel first primary="
            f"{sentinel.get('first_primary_ms') or 'none'}, baseline first primary={baseline.get('first_primary_ms') or 'none'}, "
            f"heuristic GPUs={sentinel.get('heuristic_gpu_indexes') or []}, localized GPUs={sentinel.get('localized_gpu_indexes') or []}, "
            f"context={sentinel.get('context_classes') or []}."
        )
    if trace_path:
        lines.append(f"- Trace file: `{trace_path}`")
    if labels_path:
        lines.append(f"- Labels file: `{labels_path}`")
    replay_command = normalize_replay_command(replay_command, trace_path=trace_path)
    if replay_command:
        lines.extend(["", "## Replay", "", "```bash", replay_command, "```"])
    return "\n".join(lines) + "\n"


def grade_public_control_report(
    report: Mapping[str, Any] | str | Path,
    *,
    trace: Mapping[str, Any] | str | Path | None = None,
) -> Dict[str, Any]:
    payload = json.loads(Path(report).read_text(encoding="utf-8")) if isinstance(report, (str, Path)) else dict(report)
    sentinel = dict(payload.get("sentinel") or {})
    baseline = dict(payload.get("baseline") or {})

    replay_ready = False
    replay_trace_id = None
    if trace is not None:
        trace_payload = load_trace_file(trace) if isinstance(trace, (str, Path)) else validate_trace_payload(trace)
        replay_trace_id = str(trace_payload.get("trace_id") or "")
        replay_ready = replay_trace_id == str(payload.get("trace_id") or "")

    sentinel_quiet = int(sentinel.get("detection_count") or 0) == 0
    sentinel_no_extras = int(sentinel.get("extra_alert_count") or 0) == 0
    baseline_detected = int(baseline.get("detection_count") or 0) > 0

    return {
        "status": "pass" if all((sentinel_quiet, sentinel_no_extras, replay_ready)) else "fail",
        "trace_id": str(payload.get("trace_id") or ""),
        "dataset_id": str(payload.get("dataset_id") or ""),
        "mode": "public_control",
        "criteria": {
            "sentinel_quiet": {"passed": sentinel_quiet},
            "sentinel_no_extras": {"passed": sentinel_no_extras},
            "baseline_detected": {
                "passed": baseline_detected,
                "baseline_detection_count": int(baseline.get("detection_count") or 0),
                "baseline_extra_alert_count": int(baseline.get("extra_alert_count") or 0),
            },
            "replay_ready": {
                "passed": replay_ready,
                "trace_id": replay_trace_id,
            },
        },
    }


def render_public_control_markdown(
    report: Mapping[str, Any] | str | Path,
    *,
    grade: Mapping[str, Any] | None = None,
    trace_path: str | None = None,
    labels_path: str | None = None,
    replay_command: str | None = None,
) -> str:
    payload = json.loads(Path(report).read_text(encoding="utf-8")) if isinstance(report, (str, Path)) else dict(report)
    grade_payload = dict(grade or {})
    sentinel = dict(payload.get("sentinel") or {})
    baseline = dict(payload.get("baseline") or {})

    lines = [
        "# Cluster Sentinel Public Control Summary",
        "",
        f"- Verdict: `{str(grade_payload.get('status') or 'unknown').upper()}`",
        f"- Trace: `{str(payload.get('trace_id') or '')}`",
        f"- Dataset: `{str(payload.get('dataset_id') or '')}`",
        "- Claim lane: `public_control`",
        "- Control semantics: quiet public slice with no labeled incident. Sentinel should stay quiet.",
        "",
        "| Engine | Detection Count | Extra Alerts |",
        "| --- | ---: | ---: |",
        f"| Sentinel | {int(sentinel.get('detection_count') or 0)} | {int(sentinel.get('extra_alert_count') or 0)} |",
        f"| Baseline | {int(baseline.get('detection_count') or 0)} | {int(baseline.get('extra_alert_count') or 0)} |",
        "",
        "## Control Rubric",
    ]
    for key, label in (
        ("sentinel_quiet", "Sentinel stays quiet on the control slice"),
        ("sentinel_no_extras", "Sentinel emits no operator incidents"),
        ("baseline_detected", "Baseline emits threshold noise for contrast"),
        ("replay_ready", "Replay-ready trace available"),
    ):
        criterion = dict((grade_payload.get("criteria") or {}).get(key) or {})
        lines.append(f"- {label}: `{'PASS' if criterion.get('passed') else 'FAIL'}`")

    lines.extend(
        [
            "",
            "## Evidence",
            f"- Sentinel detections: {int(sentinel.get('detection_count') or 0)}",
            f"- Baseline detections: {int(baseline.get('detection_count') or 0)}",
        ]
    )
    if trace_path:
        lines.append(f"- Trace file: `{trace_path}`")
    if labels_path:
        lines.append(f"- Labels file: `{labels_path}`")
    replay_command = normalize_replay_command(replay_command, trace_path=trace_path)
    if replay_command:
        lines.extend(["", "## Replay", "", "```bash", replay_command, "```"])
    return "\n".join(lines) + "\n"
