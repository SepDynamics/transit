"""Durable, versioned evidence records for analysis outside Valkey.

The JSONL layout is object-store friendly: each record is immutable and
partitioned by agency and UTC day.  It can be copied directly to S3-compatible
storage or queried locally with DuckDB/Parquet conversion jobs.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

EVIDENCE_SCHEMA_VERSION = "sentinel.evidence.v1"


class EvidenceArchive:
    def __init__(self, root_dir: str | Path, *, retention_days: int = 0) -> None:
        self.root_dir = Path(root_dir)
        self.retention_days = max(0, int(retention_days))
        self.last_retention_report: Dict[str, Any] = {
            "enabled": self.retention_days > 0,
            "retention_days": self.retention_days,
            "partitions_deleted": 0,
        }

    def append_snapshot(
        self,
        payload: Mapping[str, Any],
        *,
        agency_key: str,
        archive_manifest: Mapping[str, Any] | None = None,
    ) -> Path:
        health = dict(payload.get("health") or {})
        timestamp_ms = int(health.get("timestamp_ms") or _now_ms())
        captured_at = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        record = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "record_type": "operational_snapshot",
            "observed_at": captured_at.isoformat().replace("+00:00", "Z"),
            "timestamp_ms": timestamp_ms,
            "agency_key": agency_key,
            "provenance": {
                "feed_status": dict(payload.get("feed_status") or {}),
                "archive_manifest": dict(archive_manifest or {}),
                "quality": {
                    "status": health.get("status"),
                    "errors": list(payload.get("errors") or []),
                },
            },
            "observations": dict(payload.get("entities") or {}),
            "regimes": dict(payload.get("regimes") or {}),
            "incidents": dict(payload.get("incidents") or {}),
            "prediction_evidence": dict(payload.get("prediction_evidence") or {}),
        }
        partition = self.root_dir / f"agency={agency_key}" / f"date={captured_at:%Y-%m-%d}"
        partition.mkdir(parents=True, exist_ok=True)
        destination = partition / "operational_snapshots.jsonl"
        encoded = json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
        # Append through a short-lived temp file so an interrupted writer cannot
        # leave a partial JSON record in the durable dataset.
        fd, temporary_name = tempfile.mkstemp(prefix=".evidence-", dir=partition)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            with open(destination, "a", encoding="utf-8") as output, open(
                temporary_name, encoding="utf-8"
            ) as source:
                output.write(source.read())
                output.flush()
                os.fsync(output.fileno())
        finally:
            Path(temporary_name).unlink(missing_ok=True)
        self.last_retention_report = self.prune_history(
            agency_key=agency_key,
            now_ms=timestamp_ms,
        )
        return destination

    def prune_history(self, *, agency_key: str, now_ms: int) -> Dict[str, Any]:
        """Remove expired UTC date partitions for one agency."""

        report: Dict[str, Any] = {
            "enabled": self.retention_days > 0,
            "retention_days": self.retention_days,
            "partitions_examined": 0,
            "partitions_deleted": 0,
        }
        if self.retention_days <= 0:
            return report
        cutoff_date = datetime.fromtimestamp(
            int(now_ms) / 1000, tz=timezone.utc
        ).date() - timedelta(days=self.retention_days)
        report["cutoff_date"] = cutoff_date.isoformat()
        resolved_root = self.root_dir.expanduser().resolve()
        agency_dir = (resolved_root / f"agency={agency_key}").resolve()
        if agency_dir.parent != resolved_root:
            report["error"] = "invalid agency partition"
            return report
        if not agency_dir.is_dir():
            return report
        for partition in agency_dir.glob("date=*"):
            if partition.is_symlink() or not partition.is_dir():
                continue
            date_text = partition.name.removeprefix("date=")
            try:
                partition_date = datetime.strptime(date_text, "%Y-%m-%d").date()
            except ValueError:
                continue
            report["partitions_examined"] += 1
            if partition_date >= cutoff_date:
                continue
            shutil.rmtree(partition)
            report["partitions_deleted"] += 1
        return report


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)
