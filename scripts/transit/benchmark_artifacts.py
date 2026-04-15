#!/usr/bin/env python3
"""Generate benchmark artifacts under the repo's dedicated artifacts/ tree."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.shared.runtime import isoformat_ms
from scripts.transit.calibration import (
    build_transit_calibration_report,
    build_transit_calibration_suite_report,
    render_transit_calibration_markdown,
    render_transit_calibration_suite_markdown,
)
from scripts.transit.case_packs import load_case_pack_metadata, resolve_case_pack_root
from scripts.transit.report import build_archive_report


@dataclass
class TransitBenchmarkArtifactConfig:
    archive_root: Path
    labels_root: Path
    output_root: Path
    artifact_name: str
    max_snapshots: Optional[int] = None


class TransitBenchmarkArtifactService:
    def __init__(self, config: TransitBenchmarkArtifactConfig) -> None:
        self.cfg = config

    def run_once(self) -> Dict[str, Any]:
        artifact_dir = (self.cfg.output_root / self.cfg.artifact_name).resolve()
        if artifact_dir.exists():
            shutil.rmtree(artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        if self.cfg.labels_root.is_dir():
            calibration_report = build_transit_calibration_suite_report(self.cfg.archive_root, self.cfg.labels_root)
            calibration_summary = render_transit_calibration_suite_markdown(calibration_report)
            mode = "suite"
            case_pack_rows = self._build_suite_case_pack_rows(calibration_report, artifact_dir)
        else:
            calibration_report = build_transit_calibration_report(self.cfg.archive_root, self.cfg.labels_root)
            calibration_summary = render_transit_calibration_markdown(calibration_report)
            mode = "dataset"
            case_pack_rows = [self._build_dataset_case_pack_row(calibration_report, artifact_dir)]

        calibration_report_path = artifact_dir / "calibration_report.json"
        calibration_summary_path = artifact_dir / "calibration_summary.md"
        write_json(calibration_report_path, calibration_report)
        calibration_summary_path.write_text(calibration_summary, encoding="utf-8")

        manifest = {
            "generated_at": isoformat_ms(),
            "artifact_name": self.cfg.artifact_name,
            "artifact_dir": str(artifact_dir),
            "mode": mode,
            "archive_root": str(self.cfg.archive_root.resolve()),
            "labels_root": str(self.cfg.labels_root.resolve()),
            "files": {
                "calibration_report": str(calibration_report_path.relative_to(artifact_dir)),
                "calibration_summary": str(calibration_summary_path.relative_to(artifact_dir)),
            },
            "summary": build_manifest_summary(calibration_report),
            "case_packs": case_pack_rows,
        }
        manifest_path = artifact_dir / "manifest.json"
        write_json(manifest_path, manifest)
        return manifest

    def _build_suite_case_pack_rows(self, calibration_report: Dict[str, Any], artifact_dir: Path) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for pack in calibration_report.get("case_packs") or []:
            if not isinstance(pack, dict):
                continue
            case_pack_root = Path(str(pack.get("case_pack_root") or "")).resolve()
            case_pack_id = case_pack_artifact_id(pack, fallback=case_pack_root.name or "case-pack")
            pack_dir = artifact_dir / "case-packs" / case_pack_id
            pack_dir.mkdir(parents=True, exist_ok=True)
            pack_archive_report = build_archive_report(case_pack_root, max_snapshots=self.cfg.max_snapshots)
            pack_report_path = pack_dir / "calibration_report.json"
            pack_archive_path = pack_dir / "archive_report.json"
            write_json(pack_report_path, pack)
            write_json(pack_archive_path, pack_archive_report)
            rows.append(
                {
                    "case_pack_id": case_pack_id,
                    "case_pack_root": str(case_pack_root),
                    "city_key": pack.get("city_key"),
                    "event_key": pack.get("event_key"),
                    "category": pack.get("category"),
                    "label_count": int(pack.get("label_count") or 0),
                    "dataset_count": int(pack.get("dataset_count") or 0),
                    "snapshot_count": int(pack_archive_report.get("snapshot_count") or 0),
                    "value_case_supported": bool((pack.get("comparison") or {}).get("value_case_supported")),
                    "files": {
                        "calibration_report": str(pack_report_path.relative_to(artifact_dir)),
                        "archive_report": str(pack_archive_path.relative_to(artifact_dir)),
                    },
                }
            )
        return rows

    def _build_dataset_case_pack_row(self, calibration_report: Dict[str, Any], artifact_dir: Path) -> Dict[str, Any]:
        case_pack_root = resolve_case_pack_root(self.cfg.labels_root) or resolve_case_pack_root(self.cfg.archive_root) or self.cfg.archive_root
        metadata = load_case_pack_metadata(case_pack_root) if case_pack_root else {}
        case_pack_id = case_pack_artifact_id(calibration_report, fallback=str(metadata.get("case_pack_id") or case_pack_root.name or self.cfg.labels_root.stem))
        pack_dir = artifact_dir / "case-packs" / case_pack_id
        pack_dir.mkdir(parents=True, exist_ok=True)
        pack_archive_report = build_archive_report(case_pack_root, max_snapshots=self.cfg.max_snapshots)
        pack_report_path = pack_dir / "calibration_report.json"
        pack_archive_path = pack_dir / "archive_report.json"
        write_json(pack_report_path, calibration_report)
        write_json(pack_archive_path, pack_archive_report)
        return {
            "case_pack_id": case_pack_id,
            "case_pack_root": str(case_pack_root.resolve()),
            "city_key": calibration_report.get("city_key"),
            "event_key": calibration_report.get("event_key"),
            "category": calibration_report.get("category"),
            "label_count": int(calibration_report.get("label_count") or 0),
            "dataset_count": 1,
            "snapshot_count": int(pack_archive_report.get("snapshot_count") or 0),
            "value_case_supported": bool((calibration_report.get("comparison") or {}).get("value_case_supported")),
            "files": {
                "calibration_report": str(pack_report_path.relative_to(artifact_dir)),
                "archive_report": str(pack_archive_path.relative_to(artifact_dir)),
            },
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate benchmark artifacts under artifacts/benchmarks")
    parser.add_argument("--archive-root", default="data/case-packs/mbta")
    parser.add_argument("--labels", default="data/case-packs/mbta")
    parser.add_argument("--output-root", default="artifacts/benchmarks")
    parser.add_argument("--artifact-name", default="")
    parser.add_argument("--max-snapshots", type=int, default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    archive_root = Path(args.archive_root).expanduser().resolve()
    labels_root = Path(args.labels).expanduser().resolve()
    artifact_name = str(args.artifact_name or "").strip() or default_artifact_name(archive_root, labels_root)
    cfg = TransitBenchmarkArtifactConfig(
        archive_root=archive_root,
        labels_root=labels_root,
        output_root=Path(args.output_root).expanduser().resolve(),
        artifact_name=artifact_name,
        max_snapshots=(max(1, int(args.max_snapshots)) if args.max_snapshots else None),
    )
    manifest = TransitBenchmarkArtifactService(cfg).run_once()
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def build_manifest_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    comparison = dict(report.get("comparison") or {})
    summary = {
        "label_count": int(report.get("label_count") or 0),
        "value_case_supported": bool(comparison.get("value_case_supported")),
    }
    if report.get("mode") == "suite":
        summary["case_pack_count"] = int(report.get("case_pack_count") or 0)
        summary["passing_case_pack_count"] = int(comparison.get("passing_case_pack_count") or 0)
        summary["failing_case_pack_count"] = int(comparison.get("failing_case_pack_count") or 0)
    else:
        summary["dataset_id"] = report.get("dataset_id")
        summary["case_pack_id"] = report.get("case_pack_id")
    return summary


def case_pack_artifact_id(payload: Dict[str, Any], *, fallback: str) -> str:
    candidate = str(payload.get("case_pack_id") or fallback).strip()
    return slugify(candidate) or slugify(fallback) or "case-pack"


def default_artifact_name(archive_root: Path, labels_root: Path) -> str:
    if labels_root.is_file():
        return slugify(labels_root.stem) or "transit-benchmark"
    case_pack_root = resolve_case_pack_root(labels_root)
    if case_pack_root:
        metadata = load_case_pack_metadata(case_pack_root)
        candidate = str(metadata.get("case_pack_id") or case_pack_root.name).strip()
        if candidate:
            return slugify(candidate) or "transit-benchmark"
    if labels_root.name == "case-packs" or (
        labels_root.name == "mbta" and labels_root.parent.name == "case-packs"
    ):
        return "mbta-suite"
    return slugify(labels_root.name or archive_root.name) or "transit-benchmark"


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return normalized


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
