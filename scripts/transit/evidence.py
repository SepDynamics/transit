"""Durable, versioned evidence records for analysis outside Valkey.

The JSONL layout is object-store friendly: each record is immutable and
partitioned by agency and UTC day.  It can be copied directly to S3-compatible
storage or queried locally with DuckDB/Parquet conversion jobs.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

EVIDENCE_SCHEMA_VERSION = "sentinel.evidence.v1"


class EvidenceArchive:
    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)

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
        return destination


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)
