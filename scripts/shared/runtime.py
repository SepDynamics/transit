"""Shared utility helpers for runtime modules."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def isoformat_ms(timestamp_ms: Optional[int] = None) -> str:
    if timestamp_ms is None:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc).isoformat()


def scope_matches(payload: Dict[str, Any], scope: str | None) -> bool:
    if scope in ("", "all", None):
        return True
    source = str(payload.get("source") or "live")
    if scope == "live":
        return source == "live"
    if scope == "replay":
        return source == "replay"
    return source == scope
