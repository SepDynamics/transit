"""Transit snapshot calibration and proof-of-value helpers."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from scripts.transit.domain import TransitRuntimeConfig, TransitSnapshotService
from scripts.transit.snapshot_paths import resolve_snapshot_feed_paths

VALID_EXPECTED_ACTIONS = {
    "monitor",
    "hold",
    "short_turn",
    "dispatch_relief",
    "inspect_terminal",
    "warn_riders",
    "mark_feed_degraded",
}


def _expected_detection_flag(value: Any) -> bool:
    if value in (None, ""):
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in {"0", "false", "no", "absent"}


def load_transit_labels(path: str | Path) -> Dict[str, Any]:
    labels_path = Path(path)
    payload = json.loads(labels_path.read_text(encoding="utf-8"))
    normalized = normalize_transit_label_payload(payload, default_dataset_id=labels_path.stem)
    normalized["labels_path"] = str(labels_path.resolve())
    return normalized


def discover_transit_label_files(path: str | Path) -> List[Path]:
    root = Path(path)
    if root.is_file():
        return [root]
    if root.is_dir():
        files = sorted(candidate for candidate in root.rglob("*.json") if candidate.is_file())
        if root.name != "labels":
            nested_label_files = [candidate for candidate in files if "labels" in candidate.parts]
            if nested_label_files:
                files = nested_label_files
        if files:
            return files
        raise ValueError(f"no label files found under {root}")
    raise FileNotFoundError(f"labels path does not exist: {root}")


def normalize_transit_label_payload(
    payload: Mapping[str, Any],
    *,
    default_dataset_id: str = "transit-calibration",
) -> Dict[str, Any]:
    incidents = payload.get("incidents")
    if not isinstance(incidents, list):
        raise ValueError("label payload requires an incidents list")
    normalized: List[Dict[str, Any]] = []
    for index, incident in enumerate(incidents, start=1):
        if not isinstance(incident, Mapping):
            raise ValueError("incidents must be objects")
        snapshot_path = str(incident.get("snapshot_path") or "").strip()
        route_id = str(incident.get("route_id") or "").strip() or None
        entity_id = str(incident.get("entity_id") or "").strip() or None
        expected_detection = _expected_detection_flag(incident.get("expected_detection"))
        if not snapshot_path:
            raise ValueError("each incident requires snapshot_path")
        if not route_id and not entity_id:
            raise ValueError("each incident requires route_id or entity_id")
        expected_action = (
            str(incident.get("expected_action") or "monitor")
            if expected_detection
            else (
                str(incident.get("expected_action") or "").strip()
                or None
            )
        )
        if expected_action is not None and expected_action not in VALID_EXPECTED_ACTIONS:
            raise ValueError(f"unsupported expected_action: {expected_action}")
        normalized.append(
            {
                "incident_id": str(incident.get("incident_id") or f"transit-incident-{index:03d}"),
                "snapshot_path": snapshot_path,
                "entity_id": entity_id,
                "route_id": route_id,
                "expected_detection": expected_detection,
                "direction_id": (
                    int(incident["direction_id"])
                    if incident.get("direction_id") not in (None, "")
                    else None
                ),
                "expected_regime": (
                    str(incident.get("expected_regime") or "unknown")
                    if expected_detection
                    else (
                        str(incident.get("expected_regime") or "").strip()
                        or None
                    )
                ),
                "expected_action": expected_action,
                "use_case": str(incident.get("use_case") or ""),
                "note": str(incident.get("note") or ""),
            }
        )
    return {
        "dataset_id": str(payload.get("dataset_id") or default_dataset_id),
        "use_case": str(payload.get("use_case") or ""),
        "incidents": normalized,
    }


def build_transit_baseline_incidents(regimes: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    incidents: List[Dict[str, Any]] = []
    for regime in regimes:
        metrics = dict(regime.get("metrics") or {})
        route_id = regime.get("route_id")
        direction_id = metrics.get("direction_id", regime.get("direction_id"))
        baseline_regime = None
        baseline_action = "monitor"
        baseline_reasons: List[str] = []

        if float(metrics.get("feed_age_seconds") or 0.0) >= float(metrics.get("stale_after_seconds") or 90.0) and int(metrics.get("trip_update_count") or 0) == 0:
            baseline_regime = "feed_incoherent"
            baseline_action = "mark_feed_degraded"
            baseline_reasons = ["feed_age_threshold"]
        elif int(metrics.get("terminal_backlog_count") or 0) >= 3 and int(metrics.get("median_delay_seconds") or 0) >= 180:
            baseline_regime = "terminal_congestion"
            baseline_action = "inspect_terminal"
            baseline_reasons = ["terminal_backlog_threshold"]
        elif float(metrics.get("compressed_headway_share") or 0.0) >= 0.7 and int(metrics.get("median_delay_seconds") or 0) >= 180:
            baseline_regime = "headway_collapse"
            baseline_action = "dispatch_relief" if int(metrics.get("vehicle_count") or 0) >= 4 else "short_turn"
            baseline_reasons = ["compression_threshold"]
        elif int(metrics.get("active_alert_count") or 0) >= 2 or int(metrics.get("median_delay_seconds") or 0) >= 420:
            baseline_regime = "service_degraded"
            baseline_action = "warn_riders"
            baseline_reasons = ["alert_or_delay_threshold"]

        if not baseline_regime:
            continue
        incidents.append(
            {
                "entity_id": str(regime.get("entity_id") or ""),
                "label": str(regime.get("label") or ""),
                "route_id": route_id,
                "direction_id": direction_id,
                "regime": baseline_regime,
                "action": baseline_action,
                "reasons": baseline_reasons,
            }
        )
    return incidents


def build_transit_calibration_report(
    archive_root: str | Path,
    labels: Mapping[str, Any] | str | Path,
) -> Dict[str, Any]:
    label_payload = load_transit_labels(labels) if isinstance(labels, (str, Path)) else normalize_transit_label_payload(labels)
    root = Path(archive_root)
    labels_source_path = (
        Path(str(label_payload.get("labels_path") or "")).resolve()
        if label_payload.get("labels_path")
        else None
    )
    labels_by_snapshot: Dict[str, List[Dict[str, Any]]] = {}
    for incident in label_payload["incidents"]:
        labels_by_snapshot.setdefault(str(incident["snapshot_path"]), []).append(dict(incident))

    per_incident: List[Dict[str, Any]] = []
    snapshot_summaries: List[Dict[str, Any]] = []
    sentinel_match_count = 0
    baseline_match_count = 0
    sentinel_extra_alert_count = 0
    baseline_extra_alert_count = 0
    sentinel_control_violation_count = 0
    baseline_control_violation_count = 0
    sentinel_satisfied_label_count = 0
    baseline_satisfied_label_count = 0
    positive_label_count = 0
    negative_label_count = 0

    for snapshot_path, snapshot_labels in sorted(labels_by_snapshot.items()):
        snapshot_dir = _resolve_snapshot_dir(root, snapshot_path, labels_source_path)
        service = _service_for_snapshot(snapshot_dir)
        snapshot_time_ms = _snapshot_timestamp_ms(snapshot_dir)
        regimes_payload = service.regimes(now_ms=snapshot_time_ms)
        incidents_payload = service.incidents(now_ms=snapshot_time_ms)
        sentinel_incidents = [dict(row) for row in incidents_payload.get("incidents") or []]
        baseline_incidents = build_transit_baseline_incidents(regimes_payload.get("regimes") or [])

        matched_sentinel_ids = set()
        matched_baseline_ids = set()
        snapshot_sentinel_control_violations = 0
        snapshot_baseline_control_violations = 0
        for label in snapshot_labels:
            expected_detection = bool(label.get("expected_detection", True))
            if expected_detection:
                positive_label_count += 1
                sentinel_match = next((row for row in sentinel_incidents if transit_label_matches_detection(label, row)), None)
                baseline_match = next((row for row in baseline_incidents if transit_label_matches_detection(label, row)), None)
                if sentinel_match:
                    matched_sentinel_ids.add(str(sentinel_match.get("entity_id") or ""))
                    sentinel_match_count += 1
                    sentinel_satisfied_label_count += 1
                if baseline_match:
                    matched_baseline_ids.add(str(baseline_match.get("entity_id") or ""))
                    baseline_match_count += 1
                    baseline_satisfied_label_count += 1
            else:
                negative_label_count += 1
                sentinel_match = next((row for row in sentinel_incidents if transit_label_targets_detection(label, row)), None)
                baseline_match = next((row for row in baseline_incidents if transit_label_targets_detection(label, row)), None)
                if sentinel_match:
                    sentinel_control_violation_count += 1
                    snapshot_sentinel_control_violations += 1
                else:
                    sentinel_satisfied_label_count += 1
                if baseline_match:
                    baseline_control_violation_count += 1
                    snapshot_baseline_control_violations += 1
                else:
                    baseline_satisfied_label_count += 1
            per_incident.append(
                {
                    "incident_id": label["incident_id"],
                    "snapshot_path": snapshot_path,
                    "route_id": label.get("route_id"),
                    "entity_id": label.get("entity_id"),
                    "expected_detection": expected_detection,
                    "expected_regime": label["expected_regime"],
                    "expected_action": label["expected_action"],
                    "use_case": label.get("use_case") or label_payload.get("use_case") or "",
                    "sentinel_detected": bool(sentinel_match),
                    "baseline_detected": bool(baseline_match),
                    "sentinel_regime": sentinel_match.get("regime") if sentinel_match else None,
                    "baseline_regime": baseline_match.get("regime") if baseline_match else None,
                    "sentinel_action": sentinel_match.get("action") if sentinel_match else None,
                    "baseline_action": baseline_match.get("action") if baseline_match else None,
                    "sentinel_action_match": bool(
                        expected_detection
                        and sentinel_match
                        and sentinel_match.get("action") == label["expected_action"]
                    ),
                    "baseline_action_match": bool(
                        expected_detection
                        and baseline_match
                        and baseline_match.get("action") == label["expected_action"]
                    ),
                    "sentinel_label_satisfied": bool(sentinel_match) if expected_detection else not bool(sentinel_match),
                    "baseline_label_satisfied": bool(baseline_match) if expected_detection else not bool(baseline_match),
                    "note": label.get("note") or "",
                }
            )

        sentinel_extra = [
            row
            for row in sentinel_incidents
            if any(transit_label_targets_detection(label, row) for label in snapshot_labels)
            and not any(
                (
                    label.get("expected_detection", True)
                    and transit_label_matches_detection(label, row)
                )
                or (
                    not label.get("expected_detection", True)
                    and transit_label_targets_detection(label, row)
                )
                for label in snapshot_labels
            )
        ]
        baseline_extra = [
            row
            for row in baseline_incidents
            if any(transit_label_targets_detection(label, row) for label in snapshot_labels)
            and not any(
                (
                    label.get("expected_detection", True)
                    and transit_label_matches_detection(label, row)
                )
                or (
                    not label.get("expected_detection", True)
                    and transit_label_targets_detection(label, row)
                )
                for label in snapshot_labels
            )
        ]
        sentinel_extra_alert_count += len(sentinel_extra)
        baseline_extra_alert_count += len(baseline_extra)
        snapshot_summaries.append(
            {
                "snapshot_path": snapshot_path,
                "label_count": len(snapshot_labels),
                "sentinel_detection_count": len(sentinel_incidents),
                "baseline_detection_count": len(baseline_incidents),
                "sentinel_extra_alert_count": len(sentinel_extra),
                "baseline_extra_alert_count": len(baseline_extra),
                "sentinel_control_violation_count": snapshot_sentinel_control_violations,
                "baseline_control_violation_count": snapshot_baseline_control_violations,
            }
        )

    label_count = len(label_payload["incidents"])
    sentinel_action_match_count = sum(1 for row in per_incident if row["sentinel_action_match"])
    baseline_action_match_count = sum(1 for row in per_incident if row["baseline_action_match"])
    report = {
        "dataset_id": label_payload["dataset_id"],
        "use_case": label_payload.get("use_case") or "",
        "label_count": label_count,
        "positive_label_count": positive_label_count,
        "negative_label_count": negative_label_count,
        "snapshots": snapshot_summaries,
        "sentinel": {
            "matched_incident_count": sentinel_match_count,
            "extra_alert_count": sentinel_extra_alert_count,
            "control_violation_count": sentinel_control_violation_count,
            "action_match_count": sentinel_action_match_count,
            "satisfied_label_count": sentinel_satisfied_label_count,
            "precision": round(sentinel_match_count / max(1, sentinel_match_count + sentinel_extra_alert_count), 4),
            "recall": round(sentinel_match_count / max(1, positive_label_count), 4),
            "label_success_rate": round(sentinel_satisfied_label_count / max(1, label_count), 4),
        },
        "baseline": {
            "matched_incident_count": baseline_match_count,
            "extra_alert_count": baseline_extra_alert_count,
            "control_violation_count": baseline_control_violation_count,
            "action_match_count": baseline_action_match_count,
            "satisfied_label_count": baseline_satisfied_label_count,
            "precision": round(baseline_match_count / max(1, baseline_match_count + baseline_extra_alert_count), 4),
            "recall": round(baseline_match_count / max(1, positive_label_count), 4),
            "label_success_rate": round(baseline_satisfied_label_count / max(1, label_count), 4),
        },
        "comparison": {
            "matched_incident_delta": sentinel_match_count - baseline_match_count,
            "extra_alert_delta": baseline_extra_alert_count - sentinel_extra_alert_count,
            "control_violation_delta": baseline_control_violation_count - sentinel_control_violation_count,
            "action_match_delta": sentinel_action_match_count - baseline_action_match_count,
            "satisfied_label_delta": sentinel_satisfied_label_count - baseline_satisfied_label_count,
            "value_case_supported": (
                sentinel_match_count >= baseline_match_count
                and sentinel_extra_alert_count <= baseline_extra_alert_count
                and sentinel_control_violation_count <= baseline_control_violation_count
                and sentinel_action_match_count >= baseline_action_match_count
                and sentinel_satisfied_label_count >= baseline_satisfied_label_count
                and sentinel_satisfied_label_count == label_count
            ),
        },
        "per_incident": per_incident,
    }
    return report


def build_transit_calibration_suite_report(
    archive_root: str | Path,
    labels_root: str | Path,
) -> Dict[str, Any]:
    label_files = discover_transit_label_files(labels_root)
    case_packs: List[Dict[str, Any]] = []
    sentinel_match_count = 0
    baseline_match_count = 0
    sentinel_extra_alert_count = 0
    baseline_extra_alert_count = 0
    sentinel_action_match_count = 0
    baseline_action_match_count = 0
    sentinel_satisfied_label_count = 0
    baseline_satisfied_label_count = 0
    sentinel_control_violation_count = 0
    baseline_control_violation_count = 0
    label_count = 0
    pass_count = 0

    for label_file in label_files:
        report = build_transit_calibration_report(archive_root, label_file)
        case_pack = dict(report)
        case_pack["labels_path"] = str(label_file)
        case_packs.append(case_pack)
        label_count += int(report.get("label_count") or 0)
        sentinel = dict(report.get("sentinel") or {})
        baseline = dict(report.get("baseline") or {})
        comparison = dict(report.get("comparison") or {})
        sentinel_match_count += int(sentinel.get("matched_incident_count") or 0)
        baseline_match_count += int(baseline.get("matched_incident_count") or 0)
        sentinel_extra_alert_count += int(sentinel.get("extra_alert_count") or 0)
        baseline_extra_alert_count += int(baseline.get("extra_alert_count") or 0)
        sentinel_action_match_count += int(sentinel.get("action_match_count") or 0)
        baseline_action_match_count += int(baseline.get("action_match_count") or 0)
        sentinel_satisfied_label_count += int(sentinel.get("satisfied_label_count") or 0)
        baseline_satisfied_label_count += int(baseline.get("satisfied_label_count") or 0)
        sentinel_control_violation_count += int(sentinel.get("control_violation_count") or 0)
        baseline_control_violation_count += int(baseline.get("control_violation_count") or 0)
        if comparison.get("value_case_supported"):
            pass_count += 1

    fail_count = len(case_packs) - pass_count
    return {
        "mode": "suite",
        "case_pack_count": len(case_packs),
        "label_count": label_count,
        "sentinel": {
            "matched_incident_count": sentinel_match_count,
            "extra_alert_count": sentinel_extra_alert_count,
            "control_violation_count": sentinel_control_violation_count,
            "action_match_count": sentinel_action_match_count,
            "satisfied_label_count": sentinel_satisfied_label_count,
            "precision": round(sentinel_match_count / max(1, sentinel_match_count + sentinel_extra_alert_count), 4),
            "recall": round(sentinel_match_count / max(1, label_count), 4),
            "label_success_rate": round(sentinel_satisfied_label_count / max(1, label_count), 4),
        },
        "baseline": {
            "matched_incident_count": baseline_match_count,
            "extra_alert_count": baseline_extra_alert_count,
            "control_violation_count": baseline_control_violation_count,
            "action_match_count": baseline_action_match_count,
            "satisfied_label_count": baseline_satisfied_label_count,
            "precision": round(baseline_match_count / max(1, baseline_match_count + baseline_extra_alert_count), 4),
            "recall": round(baseline_match_count / max(1, label_count), 4),
            "label_success_rate": round(baseline_satisfied_label_count / max(1, label_count), 4),
        },
        "comparison": {
            "matched_incident_delta": sentinel_match_count - baseline_match_count,
            "extra_alert_delta": baseline_extra_alert_count - sentinel_extra_alert_count,
            "control_violation_delta": baseline_control_violation_count - sentinel_control_violation_count,
            "action_match_delta": sentinel_action_match_count - baseline_action_match_count,
            "satisfied_label_delta": sentinel_satisfied_label_count - baseline_satisfied_label_count,
            "passing_case_pack_count": pass_count,
            "failing_case_pack_count": fail_count,
            "value_case_supported": fail_count == 0,
        },
        "case_packs": case_packs,
    }


def render_transit_calibration_markdown(report: Mapping[str, Any]) -> str:
    payload = dict(report)
    sentinel = dict(payload.get("sentinel") or {})
    baseline = dict(payload.get("baseline") or {})
    comparison = dict(payload.get("comparison") or {})
    lines = [
        "# Transit Calibration Summary",
        "",
        f"- Dataset: `{payload.get('dataset_id')}`",
        f"- Use case: {payload.get('use_case') or 'unspecified'}",
        f"- Labels: `{int(payload.get('label_count') or 0)}`",
        f"- Positive labels: `{int(payload.get('positive_label_count') or 0)}`",
        f"- Negative labels: `{int(payload.get('negative_label_count') or 0)}`",
        "",
        "## Summary",
        "",
        "| Engine | Matched | Extra alerts | Control violations | Action matches | Label success | Precision | Recall |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| Transit Sentinel | {int(sentinel.get('matched_incident_count') or 0)} | {int(sentinel.get('extra_alert_count') or 0)} | {int(sentinel.get('control_violation_count') or 0)} | {int(sentinel.get('action_match_count') or 0)} | {float(sentinel.get('label_success_rate') or 0.0):.2f} | {float(sentinel.get('precision') or 0.0):.2f} | {float(sentinel.get('recall') or 0.0):.2f} |",
        f"| Baseline | {int(baseline.get('matched_incident_count') or 0)} | {int(baseline.get('extra_alert_count') or 0)} | {int(baseline.get('control_violation_count') or 0)} | {int(baseline.get('action_match_count') or 0)} | {float(baseline.get('label_success_rate') or 0.0):.2f} | {float(baseline.get('precision') or 0.0):.2f} | {float(baseline.get('recall') or 0.0):.2f} |",
        "",
        "## Verdict",
        "",
        f"- Value case supported: `{'PASS' if comparison.get('value_case_supported') else 'FAIL'}`",
        f"- Matched incident delta: `{int(comparison.get('matched_incident_delta') or 0)}`",
        f"- Extra alert delta: `{int(comparison.get('extra_alert_delta') or 0)}`",
        f"- Control violation delta: `{int(comparison.get('control_violation_delta') or 0)}`",
        f"- Action match delta: `{int(comparison.get('action_match_delta') or 0)}`",
        f"- Label success delta: `{int(comparison.get('satisfied_label_delta') or 0)}`",
        "",
        "## Incident Review",
        "",
    ]
    for incident in payload.get("per_incident") or []:
        item = dict(incident)
        expectation = (
            f"expected `{item.get('expected_regime')}` / `{item.get('expected_action')}`"
            if item.get("expected_detection", True)
            else "expected `no incident`"
        )
        lines.append(
            f"- `{item.get('incident_id')}` on `{item.get('snapshot_path')}`: {expectation}, "
            f"sentinel=`{item.get('sentinel_regime') or 'none'}` / `{item.get('sentinel_action') or 'none'}`, "
            f"baseline=`{item.get('baseline_regime') or 'none'}` / `{item.get('baseline_action') or 'none'}`"
        )
    return "\n".join(lines) + "\n"


def render_transit_calibration_suite_markdown(report: Mapping[str, Any]) -> str:
    payload = dict(report)
    sentinel = dict(payload.get("sentinel") or {})
    baseline = dict(payload.get("baseline") or {})
    comparison = dict(payload.get("comparison") or {})
    lines = [
        "# Transit Calibration Suite Summary",
        "",
        f"- Case packs: `{int(payload.get('case_pack_count') or 0)}`",
        f"- Labels: `{int(payload.get('label_count') or 0)}`",
        f"- Passing packs: `{int(comparison.get('passing_case_pack_count') or 0)}`",
        f"- Failing packs: `{int(comparison.get('failing_case_pack_count') or 0)}`",
        "",
        "## Aggregate Summary",
        "",
        "| Engine | Matched | Extra alerts | Control violations | Action matches | Label success | Precision | Recall |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| Transit Sentinel | {int(sentinel.get('matched_incident_count') or 0)} | {int(sentinel.get('extra_alert_count') or 0)} | {int(sentinel.get('control_violation_count') or 0)} | {int(sentinel.get('action_match_count') or 0)} | {float(sentinel.get('label_success_rate') or 0.0):.2f} | {float(sentinel.get('precision') or 0.0):.2f} | {float(sentinel.get('recall') or 0.0):.2f} |",
        f"| Baseline | {int(baseline.get('matched_incident_count') or 0)} | {int(baseline.get('extra_alert_count') or 0)} | {int(baseline.get('control_violation_count') or 0)} | {int(baseline.get('action_match_count') or 0)} | {float(baseline.get('label_success_rate') or 0.0):.2f} | {float(baseline.get('precision') or 0.0):.2f} | {float(baseline.get('recall') or 0.0):.2f} |",
        "",
        "## Case Packs",
        "",
    ]
    for case_pack in payload.get("case_packs") or []:
        pack = dict(case_pack)
        pack_comparison = dict(pack.get("comparison") or {})
        verdict = "PASS" if pack_comparison.get("value_case_supported") else "FAIL"
        lines.append(
            f"- `{pack.get('dataset_id')}` from `{pack.get('labels_path')}`: `{verdict}`, "
            f"matched delta `{int(pack_comparison.get('matched_incident_delta') or 0)}`, "
            f"extra alert delta `{int(pack_comparison.get('extra_alert_delta') or 0)}`, "
            f"action match delta `{int(pack_comparison.get('action_match_delta') or 0)}`"
        )
    return "\n".join(lines) + "\n"


def transit_label_matches_detection(label: Mapping[str, Any], detection: Mapping[str, Any]) -> bool:
    if not transit_label_targets_detection(label, detection):
        return False

    expected_regime = str(label.get("expected_regime") or "").strip()
    if expected_regime and expected_regime != "unknown":
        if str(detection.get("regime") or "") != expected_regime:
            return False
    return True


def transit_label_targets_detection(label: Mapping[str, Any], detection: Mapping[str, Any]) -> bool:
    if label.get("entity_id"):
        if str(label.get("entity_id") or "") != str(detection.get("entity_id") or ""):
            return False
    elif str(label.get("route_id") or "") != str(detection.get("route_id") or ""):
        return False
    if label.get("entity_id"):
        return True
    label_direction = label.get("direction_id")
    if label_direction is None:
        return True
    detection_direction = detection.get("direction_id")
    return detection_direction is None or int(detection_direction) == int(label_direction)


def _service_for_snapshot(snapshot_dir: Path) -> TransitSnapshotService:
    manifest = _snapshot_manifest(snapshot_dir)
    agency = str(manifest.get("agency") or "Transit Sentinel")
    default_timezone = "America/New_York" if agency.strip().upper() == "MBTA" else "UTC"
    feed_paths = resolve_snapshot_feed_paths(snapshot_dir)
    return TransitSnapshotService(
        TransitRuntimeConfig(
            system_name="MBTA Calibration",
            static_feed=feed_paths["static_gtfs"],
            vehicle_positions_feed=feed_paths["vehicle_positions"],
            trip_updates_feed=feed_paths["trip_updates"],
            alerts_feed=feed_paths["alerts"],
            feed_timezone=os.getenv("TRANSIT_FEED_TIMEZONE", default_timezone),
        )
    )


def _snapshot_timestamp_ms(snapshot_dir: Path) -> int | None:
    manifest = _snapshot_manifest(snapshot_dir)
    value = manifest.get("timestamp_ms")
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _snapshot_manifest(snapshot_dir: Path) -> Dict[str, Any]:
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _resolve_snapshot_dir(root: Path, snapshot_path: str, labels_source_path: Path | None) -> Path:
    direct = root / snapshot_path
    if direct.exists():
        return direct
    if labels_source_path is not None:
        for ancestor in [labels_source_path.parent, *labels_source_path.parents]:
            candidate = ancestor / snapshot_path
            if candidate.exists():
                return candidate
    return direct
