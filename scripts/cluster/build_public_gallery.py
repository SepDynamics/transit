#!/usr/bin/env python3
"""Build a repeatable public demo bundle from open GWDG telemetry."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cluster.evaluation import (
    build_comparison_report,
    grade_public_case_report,
    grade_public_control_report,
    render_public_case_markdown,
    render_public_control_markdown,
)
from scripts.cluster.gwdg_dataset import (
    DEFAULT_GWDG_ARCHIVE_NAME,
    build_label_payload_from_gwdg_archive,
    build_trace_from_gwdg_archive,
    download_gwdg_dataset,
)
from scripts.cluster.trace_utils import load_trace_file, validate_trace_payload, write_trace_file

DEFAULT_OFF_BUS_SELECTOR = "ggpu142_2025-02-17_gpus-fallen-off-bus"
DEFAULT_GPU_LOST_SELECTOR = "ggpu129_2026-01-19_gpu-lost"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a 3-case public demo bundle from the GWDG archive")
    parser.add_argument(
        "--dataset",
        default=str(Path("output") / "public" / DEFAULT_GWDG_ARCHIVE_NAME),
        help="Path to the local GWDG Zenodo zip archive",
    )
    parser.add_argument("--download", action="store_true", help="Download the archive from Zenodo if it is missing")
    parser.add_argument(
        "--bundle-dir",
        default=str(Path("output") / "public" / "gallery"),
        help="Directory for the generated demo bundle",
    )
    parser.add_argument("--off-bus-selector", default=DEFAULT_OFF_BUS_SELECTOR)
    parser.add_argument("--gpu-lost-selector", default=DEFAULT_GPU_LOST_SELECTOR)
    parser.add_argument(
        "--control-source-selector",
        default=DEFAULT_OFF_BUS_SELECTOR,
        help="Incident selector used to derive a quiet control slice before the first Sentinel detection",
    )
    parser.add_argument("--window-samples", type=int, default=6)
    parser.add_argument("--baseline-persistence-windows", type=int, default=3)
    parser.add_argument("--episode-cooldown-minutes", type=int, default=240)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        if not args.download:
            raise FileNotFoundError(f"dataset archive not found: {dataset_path}")
        download_gwdg_dataset(dataset_path)
        print(f"downloaded public GWDG dataset to {dataset_path}")

    bundle_dir = Path(args.bundle_dir)
    traces_dir = bundle_dir / "traces"
    labels_dir = bundle_dir / "labels"
    reports_dir = bundle_dir / "reports"
    for directory in (traces_dir, labels_dir, reports_dir):
        directory.mkdir(parents=True, exist_ok=True)

    cooldown_ms = max(0, int(args.episode_cooldown_minutes)) * 60 * 1000
    incident_cases: List[Dict[str, Any]] = []
    for case_id, selector in (
        ("off-bus", str(args.off_bus_selector)),
        ("gpu-lost", str(args.gpu_lost_selector)),
    ):
        incident_cases.append(
            build_public_case_bundle(
                dataset_path,
                selector=selector,
                case_id=case_id,
                traces_dir=traces_dir,
                labels_dir=labels_dir,
                reports_dir=reports_dir,
                window_samples=max(6, int(args.window_samples)),
                persistence_windows=max(1, int(args.baseline_persistence_windows)),
                cooldown_ms=cooldown_ms,
            )
        )

    control_source = next(
        (case for case in incident_cases if case["selector"] == str(args.control_source_selector)),
        None,
    )
    if control_source is None:
        control_source = build_public_case_bundle(
            dataset_path,
            selector=str(args.control_source_selector),
            case_id="control-source",
            traces_dir=traces_dir,
            labels_dir=labels_dir,
            reports_dir=reports_dir,
            window_samples=max(6, int(args.window_samples)),
            persistence_windows=max(1, int(args.baseline_persistence_windows)),
            cooldown_ms=cooldown_ms,
        )
    control_case = build_public_control_bundle(
        control_source,
        case_id="healthy-control",
        traces_dir=traces_dir,
        labels_dir=labels_dir,
        reports_dir=reports_dir,
        window_samples=max(6, int(args.window_samples)),
        persistence_windows=max(1, int(args.baseline_persistence_windows)),
        cooldown_ms=cooldown_ms,
    )

    gallery_manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_path": str(dataset_path),
        "bundle_dir": str(bundle_dir),
        "config": {
            "window_samples": max(6, int(args.window_samples)),
            "baseline_persistence_windows": max(1, int(args.baseline_persistence_windows)),
            "episode_cooldown_ms": cooldown_ms,
        },
        "cases": incident_cases + [control_case],
    }
    manifest_path = bundle_dir / "manifest.json"
    manifest_path.write_text(json.dumps(gallery_manifest, indent=2), encoding="utf-8")

    gallery_markdown = render_gallery_markdown(gallery_manifest)
    gallery_path = bundle_dir / "gallery.md"
    gallery_path.write_text(gallery_markdown, encoding="utf-8")

    walkthrough_path = bundle_dir / "walkthrough-notes.md"
    walkthrough_path.write_text(render_walkthrough_notes(gallery_manifest), encoding="utf-8")

    print(f"wrote public demo bundle to {bundle_dir}")
    for case in gallery_manifest["cases"]:
        print(f"{case['case_id']}: {case['summary_path']}")
    return 0


def build_public_case_bundle(
    dataset_path: Path,
    *,
    selector: str,
    case_id: str,
    traces_dir: Path,
    labels_dir: Path,
    reports_dir: Path,
    window_samples: int,
    persistence_windows: int,
    cooldown_ms: int,
) -> Dict[str, Any]:
    trace_id = f"gwdg-{selector}"
    trace = build_trace_from_gwdg_archive(dataset_path, telemetry_selector=selector, trace_id=trace_id)
    labels = build_label_payload_from_gwdg_archive(dataset_path, telemetry_selector=selector, trace=trace)
    report = build_comparison_report(
        trace,
        labels,
        window_samples=window_samples,
        persistence_windows=persistence_windows,
        episode_cooldown_ms=cooldown_ms,
    )
    grade = grade_public_case_report(report, labels=labels, trace=trace)
    replay_command = f"python3 scripts/cluster/replay.py --trace {traces_dir / f'{case_id}.json'} --scenario none --clear-trace"

    trace_path = traces_dir / f"{case_id}.json"
    labels_path = labels_dir / f"{case_id}.json"
    report_path = reports_dir / f"{case_id}.json"
    grade_path = reports_dir / f"{case_id}.public-grade.json"
    summary_path = reports_dir / f"{case_id}.public-summary.md"

    write_trace_file(trace_path, trace)
    labels_path.write_text(json.dumps(labels, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    grade_path.write_text(json.dumps(grade, indent=2), encoding="utf-8")
    summary_path.write_text(
        render_public_case_markdown(
            report,
            grade=grade,
            trace_path=str(trace_path),
            labels_path=str(labels_path),
            replay_command=replay_command,
        ),
        encoding="utf-8",
    )
    per_incident = [item for item in grade.get("per_incident", []) if isinstance(item, Mapping)]
    sentinel_case = dict((per_incident[0].get("sentinel") if per_incident else {}) or {})
    baseline_case = dict((per_incident[0].get("baseline") if per_incident else {}) or {})

    return {
        "case_id": case_id,
        "case_type": "public_case",
        "selector": selector,
        "trace_id": trace_id,
        "trace_path": str(trace_path),
        "labels_path": str(labels_path),
        "report_path": str(report_path),
        "grade_path": str(grade_path),
        "summary_path": str(summary_path),
        "replay_command": replay_command,
        "sentinel_detection_count": int(report["sentinel"]["detection_count"]),
        "baseline_detection_count": int(report["baseline"]["detection_count"]),
        "sentinel_extra_alert_count": int(report["sentinel"]["extra_alert_count"]),
        "baseline_extra_alert_count": int(report["baseline"]["extra_alert_count"]),
        "primary_detected": bool(sentinel_case.get("primary_detected")),
        "baseline_primary_detected": bool(baseline_case.get("primary_detected")),
        "highest_action": str(sentinel_case.get("highest_action") or "watch"),
        "context_classes": list(sentinel_case.get("context_classes") or []),
        "external_extra_alert_count": int(sentinel_case.get("external_extra_alert_count") or 0),
        "grade_status": str(grade["status"]),
    }


def build_public_control_bundle(
    source_case: Mapping[str, Any],
    *,
    case_id: str,
    traces_dir: Path,
    labels_dir: Path,
    reports_dir: Path,
    window_samples: int,
    persistence_windows: int,
    cooldown_ms: int,
) -> Dict[str, Any]:
    source_trace = load_trace_file(str(source_case["trace_path"]))
    source_report = json.loads(Path(str(source_case["report_path"])).read_text(encoding="utf-8"))
    first_detection_ms = min(
        int(item.get("timestamp_ms") or 0)
        for item in source_report["sentinel"]["detections"]
        if isinstance(item, Mapping)
    )
    step_ms = infer_trace_step_ms(source_trace)
    end_ms = first_detection_ms - step_ms
    control_trace = slice_trace_until(
        source_trace,
        end_ms=end_ms,
        trace_id=f"{source_trace['trace_id']}-control",
        metadata={
            "control_kind": "quiet_prefix",
            "source_trace_id": str(source_trace.get("trace_id") or ""),
            "source_case_id": str(source_case.get("case_id") or ""),
            "slice_end_ms": end_ms,
            "slice_reason": "pre_first_sentinel_detection",
        },
    )
    labels = {
        "dataset_id": f"{control_trace['trace_id']}-labels",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {
            "control_kind": "quiet_prefix",
            "source_trace_id": str(source_trace.get("trace_id") or ""),
            "slice_end_ms": end_ms,
            "expected_behavior": "sentinel_quiet",
        },
        "incidents": [],
    }
    report = build_comparison_report(
        control_trace,
        labels,
        window_samples=window_samples,
        persistence_windows=persistence_windows,
        episode_cooldown_ms=cooldown_ms,
    )
    grade = grade_public_control_report(report, trace=control_trace)
    replay_command = f"python3 scripts/cluster/replay.py --trace {traces_dir / f'{case_id}.json'} --scenario none --clear-trace"

    trace_path = traces_dir / f"{case_id}.json"
    labels_path = labels_dir / f"{case_id}.json"
    report_path = reports_dir / f"{case_id}.json"
    grade_path = reports_dir / f"{case_id}.public-control-grade.json"
    summary_path = reports_dir / f"{case_id}.public-control-summary.md"

    write_trace_file(trace_path, control_trace)
    labels_path.write_text(json.dumps(labels, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    grade_path.write_text(json.dumps(grade, indent=2), encoding="utf-8")
    summary_path.write_text(
        render_public_control_markdown(
            report,
            grade=grade,
            trace_path=str(trace_path),
            labels_path=str(labels_path),
            replay_command=replay_command,
        ),
        encoding="utf-8",
    )

    return {
        "case_id": case_id,
        "case_type": "public_control",
        "selector": str(source_case.get("selector") or ""),
        "trace_id": str(control_trace["trace_id"]),
        "trace_path": str(trace_path),
        "labels_path": str(labels_path),
        "report_path": str(report_path),
        "grade_path": str(grade_path),
        "summary_path": str(summary_path),
        "replay_command": replay_command,
        "sentinel_detection_count": int(report["sentinel"]["detection_count"]),
        "baseline_detection_count": int(report["baseline"]["detection_count"]),
        "sentinel_extra_alert_count": int(report["sentinel"]["extra_alert_count"]),
        "baseline_extra_alert_count": int(report["baseline"]["extra_alert_count"]),
        "grade_status": str(grade["status"]),
    }


def infer_trace_step_ms(trace: Mapping[str, Any]) -> int:
    normalized = validate_trace_payload(trace)
    deltas: List[int] = []
    for entity in normalized["entities"]:
        samples = entity.get("samples", [])
        for index in range(1, len(samples)):
            delta = int(samples[index]["timestamp_ms"]) - int(samples[index - 1]["timestamp_ms"])
            if delta > 0:
                deltas.append(delta)
    if not deltas:
        return 60_000
    ordered = sorted(deltas)
    return int(ordered[len(ordered) // 2])


def slice_trace_until(
    trace: Mapping[str, Any],
    *,
    end_ms: int,
    trace_id: str,
    metadata: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    normalized = validate_trace_payload(trace)
    entities: List[Dict[str, Any]] = []
    for entity in normalized["entities"]:
        samples = [sample for sample in entity.get("samples", []) if int(sample.get("timestamp_ms") or 0) <= end_ms]
        if len(samples) < 6:
            continue
        entities.append(
            {
                "host": entity["host"],
                "gpu_index": entity["gpu_index"],
                "sample": samples[-1],
                "samples": samples,
            }
        )
    if not entities:
        raise ValueError("control slice did not retain enough telemetry for any GPU")
    payload: Dict[str, Any] = {
        "trace_id": trace_id,
        "recorded_at": normalized.get("recorded_at"),
        "scope": normalized.get("scope") or "replay",
        "entities": entities,
        "metadata": dict(normalized.get("metadata") or {}),
    }
    if metadata:
        payload["metadata"].update(dict(metadata))
    return validate_trace_payload(payload)


def render_gallery_markdown(manifest: Mapping[str, Any]) -> str:
    lines = [
        "# Cluster Sentinel Public Gallery",
        "",
        "- Claim: `Sentinel compresses open GPU fault telemetry into actionable unstable/off-bus incident narratives.`",
        "- Scope: public case studies only. This gallery is not a lead-time proof benchmark.",
        "",
        "## Incident Cases",
        "",
        "| Case | Verdict | Sentinel Primary | Baseline Primary | Highest Action | Context Classes | External Extras | Raw Extras S/B |",
        "| --- | --- | ---: | ---: | --- | --- | ---: | ---: |",
    ]
    for case in manifest.get("cases", []):
        if not isinstance(case, Mapping) or str(case.get("case_type") or "") != "public_case":
            continue
        lines.append(
            f"| {case['case_id']} | {case['grade_status'].upper()} | "
            f"{'yes' if case.get('primary_detected') else 'no'} | "
            f"{'yes' if case.get('baseline_primary_detected') else 'no'} | "
            f"{str(case.get('highest_action') or 'watch')} | "
            f"{', '.join(case.get('context_classes') or []) or 'none'} | "
            f"{int(case.get('external_extra_alert_count') or 0)} | "
            f"{int(case['sentinel_extra_alert_count'])}/{int(case['baseline_extra_alert_count'])} |"
        )
    lines.extend(
        [
            "",
            "## Control",
            "",
            "| Case | Verdict | Sentinel Detections | Baseline Detections | Sentinel Extras | Baseline Extras |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for case in manifest.get("cases", []):
        if not isinstance(case, Mapping) or str(case.get("case_type") or "") != "public_control":
            continue
        lines.append(
            f"| {case['case_id']} | {case['grade_status'].upper()} | "
            f"{int(case['sentinel_detection_count'])} | {int(case['baseline_detection_count'])} | "
            f"{int(case['sentinel_extra_alert_count'])} | {int(case['baseline_extra_alert_count'])} |"
        )
    lines.extend(["", "## Artifacts"])
    for case in manifest.get("cases", []):
        if not isinstance(case, Mapping):
            continue
        lines.append(
            "- "
            f"{case['case_id']}: trace=`{case['trace_path']}`, labels=`{case['labels_path']}`, "
            f"report=`{case['report_path']}`, summary=`{case['summary_path']}`."
        )
    return "\n".join(lines) + "\n"


def render_walkthrough_notes(manifest: Mapping[str, Any]) -> str:
    cases = [case for case in manifest.get("cases", []) if isinstance(case, Mapping)]
    lines = [
        "# Public Demo Walkthrough",
        "",
        "1. Replay the off-bus case and show Sentinel surfacing the primary unstable narrative before simple thresholds label the class.",
        "2. Replay the gpu-lost case and show the same narrative compression on a second open incident type.",
        "3. Replay the healthy control slice and show Sentinel staying quiet while the threshold baseline still chatters.",
        "",
        "## Replay Commands",
    ]
    for case in cases:
        lines.append(f"- {case['case_id']}: `{case['replay_command']}`")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
