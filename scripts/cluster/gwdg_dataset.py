"""Import helpers for the public GWDG GPU telemetry dataset on Zenodo."""
from __future__ import annotations

import bz2
import csv
import io
import json
import re
import shutil
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from scripts.cluster.models import TelemetrySample
from scripts.cluster.trace_utils import (
    TRACE_FORMAT,
    TRACE_SCHEMA_VERSION,
    anonymize_trace_payload,
    iter_entity_samples,
    validate_trace_payload,
)

DEFAULT_GWDG_DOI = "10.5281/zenodo.19052367"
DEFAULT_GWDG_ARCHIVE_URL = (
    "https://zenodo.org/api/records/19052367/files/"
    "gwdg-gpu-node-telemetry-gpu-detachment-failures-2025-2026-v1.0.0.zip/content"
)
DEFAULT_GWDG_ARCHIVE_NAME = "gwdg-gpu-node-telemetry-gpu-detachment-failures-2025-2026-v1.0.0.zip"

GWDG_GPU_METRIC_FIELDS: Dict[str, Tuple[str, str]] = {
    "DCGM_FI_DEV_GPU_UTIL": ("gpu_util", "float"),
    "DCGM_FI_DEV_MEM_COPY_UTIL": ("mem_util", "float"),
    "DCGM_FI_DEV_FB_USED": ("mem_used_mb", "float"),
    "DCGM_FI_DEV_GPU_TEMP": ("temperature_c", "float"),
    "DCGM_FI_DEV_POWER_USAGE": ("power_w", "float"),
    "DCGM_FI_DEV_POWER_MGMT_LIMIT": ("power_limit_w", "float"),
    "DCGM_FI_DEV_SM_CLOCK": ("sm_clock_mhz", "float"),
    "DCGM_FI_DEV_MEM_CLOCK": ("mem_clock_mhz", "float"),
    "DCGM_FI_DEV_FAN_SPEED": ("fan_pct", "float"),
    "DCGM_FI_DEV_XID_ERRORS": ("xid_errors", "int"),
    "DCGM_FI_DEV_ECC_DBE_VOL_TOTAL": ("ecc_errors", "int_accumulate"),
    "DCGM_FI_DEV_ECC_SBE_VOL_TOTAL": ("ecc_errors", "int_accumulate"),
}

GWDG_THROTTLE_METRICS = {
    "DCGM_FI_DEV_POWER_VIOLATION": "power_cap",
    "DCGM_FI_DEV_THERMAL_VIOLATION": "thermal_violation",
    "DCGM_FI_DEV_BOARD_LIMIT_VIOLATION": "board_limit",
    "DCGM_FI_DEV_SYNC_BOOST_VIOLATION": "sync_boost",
    "DCGM_FI_DEV_LOW_UTIL_VIOLATION": "low_utilization",
}

INCIDENT_CATEGORY_MAP = {
    "gpu ecc": ("error_burst", "quarantine"),
    "gpu error/problem": ("error_burst", "quarantine"),
    "gpu lost": ("unstable", "drain"),
    "gpu fell off bus": ("unstable", "drain"),
    "gpus fell off bus": ("unstable", "drain"),
    "gpus dropped off bus": ("unstable", "drain"),
}

_INCIDENT_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def download_gwdg_dataset(destination: str | Path, *, url: str = DEFAULT_GWDG_ARCHIVE_URL) -> Path:
    output_path = Path(destination)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, output_path.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    return output_path


def list_gwdg_telemetry_members(
    archive_path: str | Path,
    *,
    include_baselines: bool = False,
) -> List[str]:
    with zipfile.ZipFile(Path(archive_path)) as archive:
        members = [
            member
            for member in archive.namelist()
            if "/telemetry/" in member and member.endswith("_tidy.csv.bz2")
        ]
    if not include_baselines:
        members = [member for member in members if "nodes_total_gpus_when_good" not in Path(member).name]
    return sorted(members)


def resolve_gwdg_telemetry_member(
    archive_path: str | Path,
    selector: str,
    *,
    include_baselines: bool = True,
) -> str:
    members = list_gwdg_telemetry_members(archive_path, include_baselines=include_baselines)
    candidate_matches: List[str] = []
    for member in members:
        filename = Path(member).name
        stem = filename.removesuffix(".csv.bz2").removesuffix(".bz2")
        if selector in {member, filename, stem, filename.removesuffix("_tidy.csv.bz2")}:
            candidate_matches.append(member)
            continue
        if filename.startswith(selector):
            candidate_matches.append(member)
    if not candidate_matches:
        raise FileNotFoundError(f"telemetry member not found for selector: {selector}")
    if len(candidate_matches) > 1:
        raise ValueError(f"telemetry selector is ambiguous: {selector} -> {candidate_matches}")
    return candidate_matches[0]


def build_trace_from_gwdg_archive(
    archive_path: str | Path,
    *,
    telemetry_selector: str,
    trace_id: str | None = None,
    anonymize: bool = False,
) -> Dict[str, Any]:
    archive_path = Path(archive_path)
    telemetry_member = resolve_gwdg_telemetry_member(archive_path, telemetry_selector)
    root_prefix = telemetry_member.split("/telemetry/", 1)[0]
    meta_member = telemetry_member.replace("/telemetry/", "/metadata/").replace("_tidy.csv.bz2", "_meta.json")
    meta_payload = _load_json_member(archive_path, meta_member) if _member_exists(archive_path, meta_member) else {}
    raw_samples: Dict[Tuple[str, int, int], Dict[str, Any]] = {}

    for row in _iter_csv_rows(archive_path, telemetry_member):
        gpu_value = str(row.get("gpu") or "").strip()
        if gpu_value in {"", "nan"}:
            continue
        metric_name = str(row.get("metric") or "").strip()
        value = _parse_float(row.get("value"))
        if metric_name == "" or value is None:
            continue
        host = str(row.get("node") or "unknown-host")
        gpu_index = int(float(gpu_value))
        timestamp_ms = _parse_utc_timestamp_ms(str(row.get("timeUtc") or ""))
        key = (host, gpu_index, timestamp_ms)
        sample = raw_samples.setdefault(
            key,
            {
                "timestamp_ms": timestamp_ms,
                "host": host,
                "gpu_index": gpu_index,
                "uuid": str(row.get("uuid") or ""),
                "name": str(row.get("modelName") or f"GPU {gpu_index}"),
                "gpu_util": 0.0,
                "mem_util": 0.0,
                "mem_used_mb": 0.0,
                "mem_total_mb": 0.0,
                "temperature_c": None,
                "power_w": None,
                "power_limit_w": None,
                "sm_clock_mhz": None,
                "mem_clock_mhz": None,
                "fan_pct": None,
                "ecc_errors": 0,
                "xid_errors": 0,
                "throttle_reasons": [],
                "pcie_tx": None,
                "pcie_rx": None,
                "source": "replay",
                "collection_source": "gwdg_zenodo",
                "trace_id": trace_id or _default_trace_id(telemetry_member),
                "_fb_free_mb": None,
            },
        )
        if not sample["uuid"] and row.get("uuid"):
            sample["uuid"] = str(row["uuid"])
        if metric_name == "DCGM_FI_DEV_FB_FREE":
            sample["_fb_free_mb"] = float(value)
            continue
        if metric_name in GWDG_GPU_METRIC_FIELDS:
            field_name, field_kind = GWDG_GPU_METRIC_FIELDS[metric_name]
            if field_kind == "float":
                sample[field_name] = float(value)
            elif field_kind == "int":
                sample[field_name] = int(value)
            elif field_kind == "int_accumulate":
                sample[field_name] = int(sample.get(field_name) or 0) + int(value)
            continue
        if metric_name in GWDG_THROTTLE_METRICS and value > 0:
            reason = GWDG_THROTTLE_METRICS[metric_name]
            if reason not in sample["throttle_reasons"]:
                sample["throttle_reasons"].append(reason)

    entities: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    for sample in raw_samples.values():
        fb_free_mb = _parse_float(sample.pop("_fb_free_mb", None))
        if fb_free_mb is not None and sample["mem_total_mb"] == 0.0:
            sample["mem_total_mb"] = max(0.0, float(sample["mem_used_mb"]) + fb_free_mb)
        if sample["mem_util"] == 0.0 and sample["mem_total_mb"] > 0.0:
            sample["mem_util"] = min(100.0, (float(sample["mem_used_mb"]) / float(sample["mem_total_mb"])) * 100.0)
        normalized_sample = TelemetrySample.from_mapping(sample).to_json()
        entities.setdefault((normalized_sample["host"], normalized_sample["gpu_index"]), []).append(normalized_sample)

    if not entities:
        raise ValueError(f"no GPU telemetry rows found in {telemetry_member}")

    normalized_entities: List[Dict[str, Any]] = []
    for (host, gpu_index), samples in sorted(entities.items()):
        ordered_samples = sorted(samples, key=lambda item: int(item["timestamp_ms"]))
        normalized_entities.append(
            {
                "host": host,
                "gpu_index": gpu_index,
                "sample": ordered_samples[-1],
                "samples": ordered_samples,
            }
        )

    payload: Dict[str, Any] = {
        "trace_format": TRACE_FORMAT,
        "schema_version": TRACE_SCHEMA_VERSION,
        "trace_id": str(trace_id or _default_trace_id(telemetry_member)),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "scope": "replay",
        "entities": normalized_entities,
        "metadata": {
            "source_type": "gwdg_tidy_csv",
            "collection_source": "gwdg_zenodo",
            "dataset_doi": DEFAULT_GWDG_DOI,
            "dataset_archive_url": DEFAULT_GWDG_ARCHIVE_URL,
            "archive_path": str(archive_path),
            "dataset_root": root_prefix,
            "telemetry_member": telemetry_member,
            "meta_member": meta_member if meta_payload else None,
            "node": str(meta_payload.get("node") or normalized_entities[0]["host"]),
            "input_type": str(meta_payload.get("inputType") or "label-heavy-prometheus"),
            "notes": str(meta_payload.get("notes") or ""),
        },
    }
    normalized_trace = validate_trace_payload(payload)
    return anonymize_trace_payload(normalized_trace) if anonymize else normalized_trace


def build_label_payload_from_gwdg_archive(
    archive_path: str | Path,
    *,
    telemetry_selector: str,
    trace: Mapping[str, Any],
    dataset_id: str | None = None,
) -> Dict[str, Any]:
    telemetry_member = resolve_gwdg_telemetry_member(archive_path, telemetry_selector)
    trace_payload = validate_trace_payload(trace)
    incident = _match_incident_event(archive_path, telemetry_member, trace_payload)
    incident_class, expected_action = map_gwdg_incident_category(str(incident.get("category") or ""))
    incident_gpus = infer_gwdg_incident_gpus(trace_payload)
    trace_start_ms = min(int(entity["samples"][0]["timestamp_ms"]) for entity in trace_payload["entities"])
    trace_end_ms = max(int(entity["samples"][-1]["timestamp_ms"]) for entity in trace_payload["entities"])
    collect_start_ms = _parse_utc_timestamp_ms(str(incident.get("collectStart") or ""))
    collect_end_ms = _parse_utc_timestamp_ms(str(incident.get("collectEnd") or ""))
    onset_ms = min(trace_start_ms, collect_start_ms)
    end_ms = max(trace_end_ms, collect_end_ms)
    if end_ms <= onset_ms:
        raise ValueError(f"invalid incident window for {telemetry_member}")
    host = str(trace_payload["entities"][0]["host"])
    incidents = [
        {
            "incident_id": _default_trace_id(telemetry_member),
            "trace_id": str(trace_payload.get("trace_id") or ""),
            "host": host,
            "gpu_index": int(incident_gpus[0]) if incident_gpus else 0,
            "incident_class": incident_class,
            "onset_ms": onset_ms,
            "end_ms": end_ms,
            "onset_precision": "coarse_window",
            "label_granularity": "node_window",
            "expected_action": expected_action,
            "expected_summary": str(incident.get("category") or Path(telemetry_member).name),
        }
    ]
    return {
        "dataset_id": str(dataset_id or f"{trace_payload.get('trace_id') or _default_trace_id(telemetry_member)}-labels"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {
            "source_dataset": "gwdg_zenodo",
            "dataset_doi": DEFAULT_GWDG_DOI,
            "label_granularity": "node_window",
            "onset_precision": "coarse_window",
            "heuristic_gpu_indexes": [int(gpu_index) for gpu_index in incident_gpus],
        },
        "incidents": incidents,
    }


def infer_gwdg_incident_gpus(trace: Mapping[str, Any]) -> List[int]:
    normalized = validate_trace_payload(trace)
    entity_windows = list(iter_entity_samples(normalized))
    if not entity_windows:
        return [0]
    global_last_ms = max(samples[-1].timestamp_ms for _, samples in entity_windows if samples)
    max_sample_count = max(len(samples) for _, samples in entity_windows if samples)
    signal_gpus: List[int] = []
    dropout_gpus: List[int] = []
    best_gpu = int(entity_windows[0][0]["gpu_index"])
    best_score = float("-inf")

    for entity, samples in entity_windows:
        if not samples:
            continue
        gpu_index = int(entity["gpu_index"])
        xid_spike = _max_counter_spike(sample.xid_errors for sample in samples)
        ecc_spike = _max_counter_spike(sample.ecc_errors for sample in samples)
        xid_peak = max(sample.xid_errors for sample in samples)
        ecc_peak = max(sample.ecc_errors for sample in samples)
        cadence_ms = _median_step_ms(samples)
        last_gap_ms = max(0, global_last_ms - samples[-1].timestamp_ms)
        sample_gap = max(0, max_sample_count - len(samples))
        score = float((max(xid_spike, xid_peak) * 100) + (max(ecc_spike, ecc_peak) * 25) + sample_gap)
        if cadence_ms > 0:
            score += float(last_gap_ms / cadence_ms)
        if xid_spike > 0 or xid_peak > 0 or ecc_spike > 0 or ecc_peak > 0:
            signal_gpus.append(gpu_index)
        elif cadence_ms > 0 and last_gap_ms >= cadence_ms * 2:
            dropout_gpus.append(gpu_index)
        if score > best_score or (score == best_score and gpu_index < best_gpu):
            best_score = score
            best_gpu = gpu_index

    if signal_gpus:
        return sorted(set(signal_gpus))
    if dropout_gpus:
        return sorted(set(dropout_gpus))
    return [best_gpu]


def map_gwdg_incident_category(category: str) -> Tuple[str, str]:
    normalized = category.strip().lower()
    for token, mapped in INCIDENT_CATEGORY_MAP.items():
        if normalized == token:
            return mapped
    if "ecc" in normalized or "error" in normalized or "problem" in normalized:
        return ("error_burst", "quarantine")
    if "lost" in normalized or "bus" in normalized:
        return ("unstable", "drain")
    return ("unstable", "alert")


def _match_incident_event(
    archive_path: str | Path,
    telemetry_member: str,
    trace: Mapping[str, Any],
) -> Dict[str, Any]:
    host = str(trace["entities"][0]["host"])
    trace_start_ms = min(int(entity["samples"][0]["timestamp_ms"]) for entity in trace["entities"])
    trace_end_ms = max(int(entity["samples"][-1]["timestamp_ms"]) for entity in trace["entities"])
    file_date = _extract_member_date(telemetry_member)
    candidates = [row for row in _iter_csv_rows(archive_path, _incident_member_name(archive_path)) if str(row.get("node") or "") == host]
    if file_date:
        dated_candidates = []
        for row in candidates:
            incident_day = _parse_incident_date(str(row.get("incidentDate") or "")).strftime("%Y-%m-%d")
            if incident_day == file_date:
                dated_candidates.append(row)
        if dated_candidates:
            candidates = dated_candidates
    if not candidates:
        raise ValueError(f"no incident rows found for host {host} in {telemetry_member}")
    best_row = candidates[0]
    best_overlap = -1
    for row in candidates:
        collect_start_ms = _parse_utc_timestamp_ms(str(row.get("collectStart") or ""))
        collect_end_ms = _parse_utc_timestamp_ms(str(row.get("collectEnd") or ""))
        overlap = max(0, min(trace_end_ms, collect_end_ms) - max(trace_start_ms, collect_start_ms))
        if overlap > best_overlap:
            best_overlap = overlap
            best_row = row
    return best_row


def _default_trace_id(telemetry_member: str) -> str:
    return f"gwdg-{Path(telemetry_member).name.removesuffix('_tidy.csv.bz2')}"


def _extract_member_date(telemetry_member: str) -> str | None:
    match = _INCIDENT_DATE_RE.search(Path(telemetry_member).name)
    return match.group(1) if match else None


def _incident_member_name(archive_path: str | Path) -> str:
    with zipfile.ZipFile(Path(archive_path)) as archive:
        for member in archive.namelist():
            if member.endswith("/incident_events.csv") or Path(member).name == "incident_events.csv":
                return member
    raise FileNotFoundError("incident_events.csv not found in archive")


def _member_exists(archive_path: str | Path, member_name: str) -> bool:
    with zipfile.ZipFile(Path(archive_path)) as archive:
        return member_name in set(archive.namelist())


def _load_json_member(archive_path: str | Path, member_name: str) -> Dict[str, Any]:
    with zipfile.ZipFile(Path(archive_path)) as archive:
        return json.loads(archive.read(member_name))


def _iter_csv_rows(archive_path: str | Path, member_name: str) -> Iterable[Dict[str, str]]:
    with zipfile.ZipFile(Path(archive_path)) as archive:
        raw_bytes = archive.read(member_name)
    if member_name.endswith(".bz2"):
        raw_bytes = bz2.decompress(raw_bytes)
    text = raw_bytes.decode("utf-8")
    yield from csv.DictReader(io.StringIO(text))


def _parse_float(value: Any) -> float | None:
    if value in (None, "", "nan", "NaN", "[Not Supported]"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_utc_timestamp_ms(value: str) -> int:
    dt = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _parse_incident_date(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%d %b %Y").replace(tzinfo=timezone.utc)


def _median_step_ms(samples: Sequence[TelemetrySample]) -> int:
    if len(samples) < 2:
        return 0
    deltas = [
        max(0, samples[index].timestamp_ms - samples[index - 1].timestamp_ms)
        for index in range(1, len(samples))
        if samples[index].timestamp_ms > samples[index - 1].timestamp_ms
    ]
    if not deltas:
        return 0
    ordered = sorted(deltas)
    return int(ordered[len(ordered) // 2])


def _max_counter_spike(values: Iterable[int]) -> int:
    previous = None
    max_spike = 0
    for value in values:
        current = int(value)
        if previous is not None:
            max_spike = max(max_spike, max(0, current - previous))
        previous = current
    return max_spike
