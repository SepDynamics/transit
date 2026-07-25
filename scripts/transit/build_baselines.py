#!/usr/bin/env python3
"""Build route/time reliability baselines from durable evidence JSONL."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Sentinel reliability baselines")
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--agency", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    buckets: Dict[tuple[str, str, int, int], list[Dict[str, float]]] = defaultdict(list)
    root = Path(args.evidence_root) / f"agency={args.agency}"
    for path in sorted(root.glob("date=*/operational_snapshots.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            observed = datetime.fromisoformat(record["observed_at"].replace("Z", "+00:00"))
            day_type = "weekend" if observed.weekday() >= 5 else "weekday"
            for corridor in (record.get("observations") or {}).get("lines") or []:
                if not isinstance(corridor, dict):
                    continue
                route_id = str(corridor.get("route_id") or "")
                if not route_id:
                    continue
                direction = int(corridor.get("direction_id") or -1)
                buckets[(route_id, day_type, direction, observed.hour)].append(
                    {
                        "hazard": float(corridor.get("avg_hazard") or 0),
                        "delay_seconds": float(corridor.get("avg_delay_seconds") or 0),
                    }
                )
    rows = []
    for (route_id, day_type, direction, hour), values in sorted(buckets.items()):
        rows.append({
            "route_id": route_id, "day_type": day_type, "direction_id": direction,
            "hour_utc": hour, "sample_count": len(values),
            "avg_hazard": round(sum(v["hazard"] for v in values) / len(values), 4),
            "avg_delay_seconds": round(sum(v["delay_seconds"] for v in values) / len(values), 1),
        })
    output = {"schema_version": "sentinel.reliability_baseline.v1", "agency_key": args.agency, "baselines": rows}
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
