"""Trace schema, anonymization, and import helpers for Cluster Sentinel."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from scripts.cluster.models import TelemetrySample
from scripts.cluster.telemetry_sources import parse_prometheus_metrics

TRACE_FORMAT = "cluster-sentinel.trace/v1"
TRACE_SCHEMA_VERSION = 1
ANONYMIZED_KEY_PREFIXES = {
    "host": "node",
    "hostname": "node",
    "uuid": "GPU-ANON",
    "job": "job",
    "job_name": "job",
    "namespace": "ns",
    "pod": "pod",
    "user": "user",
}


def validate_trace_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("trace payload must be an object")
    entities_payload = payload.get("entities")
    if not isinstance(entities_payload, list):
        raise ValueError("trace payload requires an entities list")

    normalized_entities: List[Dict[str, Any]] = []
    for raw_entity in entities_payload:
        if not isinstance(raw_entity, Mapping):
            raise ValueError("trace entities must be objects")
        raw_samples = raw_entity.get("samples")
        if not isinstance(raw_samples, list) or not raw_samples:
            raise ValueError("trace entity requires a non-empty samples list")
        entity_host = str(
            raw_entity.get("host")
            or (raw_entity.get("sample") or {}).get("host")
            or raw_samples[0].get("host")
            or "unknown-host"
        )
        entity_gpu = int(
            raw_entity.get("gpu_index")
            or (raw_entity.get("sample") or {}).get("gpu_index")
            or raw_samples[0].get("gpu_index")
            or 0
        )
        samples: List[Dict[str, Any]] = []
        for raw_sample in raw_samples:
            if not isinstance(raw_sample, Mapping):
                raise ValueError("trace samples must be objects")
            sample_payload = dict(raw_sample)
            sample_payload.setdefault("host", entity_host)
            sample_payload.setdefault("gpu_index", entity_gpu)
            samples.append(TelemetrySample.from_mapping(sample_payload).to_json())
        samples.sort(key=lambda item: int(item.get("timestamp_ms") or 0))

        latest_payload = dict(raw_entity.get("sample") or samples[-1])
        latest_payload.setdefault("host", entity_host)
        latest_payload.setdefault("gpu_index", entity_gpu)
        latest = TelemetrySample.from_mapping(latest_payload).to_json()

        normalized_entities.append(
            {
                "host": entity_host,
                "gpu_index": entity_gpu,
                "sample": latest,
                "samples": samples,
            }
        )

    normalized_entities.sort(key=lambda item: (str(item["host"]), int(item["gpu_index"])))
    normalized: Dict[str, Any] = {
        "trace_format": str(payload.get("trace_format") or TRACE_FORMAT),
        "schema_version": int(payload.get("schema_version") or TRACE_SCHEMA_VERSION),
        "trace_id": str(payload.get("trace_id") or "trace"),
        "recorded_at": str(payload.get("recorded_at") or datetime.now(timezone.utc).isoformat()),
        "scope": str(payload.get("scope") or "live"),
        "entities": normalized_entities,
    }
    if isinstance(payload.get("metadata"), Mapping):
        normalized["metadata"] = copy.deepcopy(dict(payload["metadata"]))
    return normalized


def load_trace_file(path: str | Path) -> Dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_trace_payload(payload)


def write_trace_file(path: str | Path, payload: Mapping[str, Any]) -> Dict[str, Any]:
    normalized = validate_trace_payload(payload)
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    return normalized


def anonymize_trace_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    normalized = validate_trace_payload(payload)
    mappings: Dict[str, Dict[str, str]] = {key: {} for key in ANONYMIZED_KEY_PREFIXES}

    def anonymize_value(key: str, value: Any) -> Any:
        if key not in ANONYMIZED_KEY_PREFIXES or value in (None, ""):
            return value
        token = str(value)
        bucket = mappings[key]
        if token not in bucket:
            prefix = ANONYMIZED_KEY_PREFIXES[key]
            bucket[token] = f"{prefix}-{len(bucket) + 1:03d}"
        return bucket[token]

    def walk(value: Any) -> Any:
        if isinstance(value, Mapping):
            result: Dict[str, Any] = {}
            for key, item in value.items():
                if isinstance(item, (Mapping, list)):
                    result[str(key)] = walk(item)
                else:
                    result[str(key)] = anonymize_value(str(key), item)
            return result
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value

    return validate_trace_payload(walk(normalized))


def build_trace_from_prometheus_snapshots(
    snapshots: Sequence[Tuple[int, str]],
    *,
    trace_id: str,
    scope: str = "live",
    collection_source: str = "dcgm_exporter",
    host: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    anonymize: bool = False,
) -> Dict[str, Any]:
    if not snapshots:
        raise ValueError("at least one snapshot is required")
    ordered = sorted((int(timestamp_ms), str(text)) for timestamp_ms, text in snapshots)
    entities: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    for timestamp_ms, text in ordered:
        samples = parse_prometheus_metrics(
            text,
            timestamp_ms=timestamp_ms,
            host=host,
            source="live",
            collection_source=collection_source,
        )
        for sample in samples:
            entities.setdefault((sample.host, sample.gpu_index), []).append(sample.to_json())

    payload: Dict[str, Any] = {
        "trace_format": TRACE_FORMAT,
        "schema_version": TRACE_SCHEMA_VERSION,
        "trace_id": trace_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "scope": scope,
        "entities": [
            {
                "host": entity_host,
                "gpu_index": gpu_index,
                "sample": samples[-1],
                "samples": samples,
            }
            for (entity_host, gpu_index), samples in sorted(entities.items())
        ],
        "metadata": {
            "source_type": "prometheus_snapshots",
            "collection_source": collection_source,
            "snapshot_count": len(ordered),
        },
    }
    if metadata:
        payload["metadata"].update(copy.deepcopy(dict(metadata)))
    normalized = validate_trace_payload(payload)
    return anonymize_trace_payload(normalized) if anonymize else normalized


def iter_entity_samples(trace: Mapping[str, Any]) -> Iterable[Tuple[Dict[str, Any], List[TelemetrySample]]]:
    normalized = validate_trace_payload(trace)
    for entity in normalized["entities"]:
        yield entity, [TelemetrySample.from_mapping(sample) for sample in entity.get("samples", [])]
