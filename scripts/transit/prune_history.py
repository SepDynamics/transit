#!/usr/bin/env python3
"""Trim rolling Transit Sentinel history keys in Valkey."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import redis  # type: ignore
except ImportError:  # pragma: no cover
    redis = None


DEFAULT_PATTERNS = (
    "transit:vehicle:history:*",
    "transit:corridor:history:*",
)


def main() -> int:
    args = build_parser().parse_args()
    if redis is None:
        raise RuntimeError("redis dependency is not installed")
    client = redis.from_url(str(args.redis), decode_responses=True)
    result = prune_history(
        client,
        retention=max(1, int(args.retention)),
        patterns=tuple(args.pattern or DEFAULT_PATTERNS),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trim rolling transit history sorted sets to a bounded retention."
    )
    parser.add_argument(
        "--redis",
        default=os.getenv("VALKEY_URL", os.getenv("REDIS_URL", "redis://localhost:6379/0")),
    )
    parser.add_argument(
        "--retention",
        type=int,
        default=int(os.getenv("TRANSIT_HISTORY_RETENTION", "120")),
        help="number of newest sorted-set rows to keep per history key",
    )
    parser.add_argument(
        "--pattern",
        action="append",
        help="Valkey key pattern to scan; can be repeated",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def prune_history(
    client: Any,
    *,
    retention: int,
    patterns: tuple[str, ...],
    dry_run: bool = False,
) -> dict[str, Any]:
    keys = sorted(
        {
            str(key)
            for pattern in patterns
            for key in client.scan_iter(match=pattern, count=500)
            if key
        }
    )
    examined = 0
    trimmed_keys = 0
    removed_rows = 0
    samples = []
    pipe = client.pipeline()
    pending = 0
    for key in keys:
        key_type = client.type(key)
        if key_type != "zset":
            continue
        examined += 1
        count = int(client.zcard(key) or 0)
        overflow = max(0, count - retention)
        if overflow <= 0:
            continue
        trimmed_keys += 1
        removed_rows += overflow
        if len(samples) < 12:
            samples.append({"key": key, "rows": count, "remove": overflow})
        if not dry_run:
            pipe.zremrangebyrank(key, 0, overflow - 1)
            pending += 1
            if pending >= 250:
                pipe.execute()
                pending = 0
    if not dry_run and pending:
        pipe.execute()
    return {
        "dry_run": dry_run,
        "retention": retention,
        "patterns": list(patterns),
        "history_keys_examined": examined,
        "history_keys_trimmed": trimmed_keys,
        "rows_removed": removed_rows,
        "samples": samples,
    }


if __name__ == "__main__":
    raise SystemExit(main())
