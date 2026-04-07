"""
Public service-status severity rules and rider-facing wording templates.

This module translates internal regime/action signals into a stable public
severity tier and plain-language summaries suitable for:
  - rider-facing status pages
  - digital signage
  - third-party app integrations
  - agency comms teams

Severity tiers
--------------
GOOD        Normal service. No action required.
ADVISORY    Minor degradation. Riders should allow extra time.
DELAY       Measurable delay on a corridor. Specific action underway.
DISRUPTION  Significant service disruption. Riders should check alternatives.
SEVERE      Major disruption. Corridor-level impact is expected to persist.

These tiers map to internal regimes and actions but intentionally do not
expose the internal scoring vocabulary directly to the public surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Severity tier
# ---------------------------------------------------------------------------

SEVERITY_GOOD = "good"
SEVERITY_ADVISORY = "advisory"
SEVERITY_DELAY = "delay"
SEVERITY_DISRUPTION = "disruption"
SEVERITY_SEVERE = "severe"
SEVERITY_UNKNOWN = "unknown"

SEVERITY_ORDER = [
    SEVERITY_GOOD,
    SEVERITY_ADVISORY,
    SEVERITY_DELAY,
    SEVERITY_DISRUPTION,
    SEVERITY_SEVERE,
]

SEVERITY_LABELS: Dict[str, str] = {
    SEVERITY_GOOD: "Good Service",
    SEVERITY_ADVISORY: "Service Advisory",
    SEVERITY_DELAY: "Delays",
    SEVERITY_DISRUPTION: "Service Disruption",
    SEVERITY_SEVERE: "Major Disruption",
    SEVERITY_UNKNOWN: "Status Unavailable",
}

SEVERITY_COLOR: Dict[str, str] = {
    SEVERITY_GOOD: "green",
    SEVERITY_ADVISORY: "yellow",
    SEVERITY_DELAY: "orange",
    SEVERITY_DISRUPTION: "red",
    SEVERITY_SEVERE: "red",
    SEVERITY_UNKNOWN: "gray",
}


def severity_rank(tier: str) -> int:
    """Return a sortable integer rank for a severity tier. Higher is worse."""
    try:
        return SEVERITY_ORDER.index(tier)
    except ValueError:
        return -1


# ---------------------------------------------------------------------------
# Regime → severity mapping
# ---------------------------------------------------------------------------

_REGIME_TO_SEVERITY: Dict[str, str] = {
    # Internal regime name → public severity tier
    "healthy": SEVERITY_GOOD,
    "recovering": SEVERITY_ADVISORY,
    "data_sparse": SEVERITY_ADVISORY,
    "corridor_unstable": SEVERITY_DELAY,
    "bunching_onset": SEVERITY_DELAY,
    "headway_collapse": SEVERITY_DISRUPTION,
    "service_degraded": SEVERITY_DISRUPTION,
    "terminal_blocked": SEVERITY_SEVERE,
    # Catch-all for any new regimes not yet mapped
}

_ACTION_TO_SEVERITY: Dict[str, str] = {
    # Some actions independently signal higher severity
    "dispatch_relief": SEVERITY_DISRUPTION,
    "short_turn": SEVERITY_DISRUPTION,
    "hold": SEVERITY_DELAY,
    "inspect_terminal": SEVERITY_DELAY,
    "warn_riders": SEVERITY_ADVISORY,
    "mark_feed_degraded": SEVERITY_ADVISORY,
    "monitor": SEVERITY_GOOD,
}


def classify_severity(
    regime: Optional[str],
    action: Optional[str],
    hazard: Optional[float],
    *,
    active_alert_count: int = 0,
) -> str:
    """Derive a public severity tier from internal scoring outputs.

    The logic takes the maximum severity implied by the regime, the action,
    and the raw hazard score, then escalates if there are active alerts.
    """
    tiers: List[str] = []

    regime_tier = _REGIME_TO_SEVERITY.get(str(regime or ""), SEVERITY_UNKNOWN)
    if regime_tier != SEVERITY_UNKNOWN:
        tiers.append(regime_tier)

    action_tier = _ACTION_TO_SEVERITY.get(str(action or ""), None)
    if action_tier:
        tiers.append(action_tier)

    # Hazard score directly implies severity if no regime match
    if isinstance(hazard, (int, float)) and hazard is not None:
        if hazard >= 0.85:
            tiers.append(SEVERITY_SEVERE)
        elif hazard >= 0.65:
            tiers.append(SEVERITY_DISRUPTION)
        elif hazard >= 0.45:
            tiers.append(SEVERITY_DELAY)
        elif hazard >= 0.2:
            tiers.append(SEVERITY_ADVISORY)

    if active_alert_count >= 3:
        tiers.append(SEVERITY_DELAY)
    elif active_alert_count >= 1:
        tiers.append(SEVERITY_ADVISORY)

    if not tiers:
        return SEVERITY_GOOD

    return max(tiers, key=severity_rank)


# ---------------------------------------------------------------------------
# Wording templates
# ---------------------------------------------------------------------------

# Templates use {route} as the corridor label placeholder.
_WORDING_TEMPLATES: Dict[str, Dict[str, str]] = {
    SEVERITY_GOOD: {
        "headline": "{route}: Good service",
        "body": "{route} is operating normally.",
        "short": "Normal service",
    },
    SEVERITY_ADVISORY: {
        "headline": "{route}: Service advisory",
        "body": "Minor irregularities detected on {route}. Allow extra travel time.",
        "short": "Minor irregularity",
    },
    SEVERITY_DELAY: {
        "headline": "{route}: Delays",
        "body": "Delays are reported on {route}. Plan for additional travel time.",
        "short": "Delays reported",
    },
    SEVERITY_DISRUPTION: {
        "headline": "{route}: Service disruption",
        "body": (
            "Service on {route} is significantly disrupted. "
            "Check the real-time feed for the latest information."
        ),
        "short": "Service disruption",
    },
    SEVERITY_SEVERE: {
        "headline": "{route}: Major disruption",
        "body": (
            "Major disruption on {route}. Expect significant delays or service gaps. "
            "Consider alternate routes."
        ),
        "short": "Major disruption",
    },
    SEVERITY_UNKNOWN: {
        "headline": "{route}: Status unavailable",
        "body": "Current status for {route} is not available. Check back shortly.",
        "short": "Status unavailable",
    },
}


def build_wording(severity: str, route_label: str) -> Dict[str, str]:
    """Return rendered wording strings for a given severity and route label."""
    templates = _WORDING_TEMPLATES.get(severity, _WORDING_TEMPLATES[SEVERITY_UNKNOWN])
    label = str(route_label or "This route")
    return {
        "headline": templates["headline"].format(route=label),
        "body": templates["body"].format(route=label),
        "short": templates["short"],
    }


# ---------------------------------------------------------------------------
# Network severity summary
# ---------------------------------------------------------------------------


def classify_network_severity(route_severities: List[str]) -> str:
    """Return a single network-level severity from a list of route severities."""
    if not route_severities:
        return SEVERITY_GOOD
    return max(route_severities, key=severity_rank)


# ---------------------------------------------------------------------------
# Public route status record
# ---------------------------------------------------------------------------


@dataclass
class RouteStatus:
    """Rider-facing status record for a single route/corridor."""

    entity_id: str
    route_id: Optional[str]
    direction_id: Optional[int]
    label: str
    severity: str
    severity_label: str
    severity_color: str
    headline: str
    body: str
    short_summary: str
    hazard_score: Optional[float]
    regime: Optional[str]
    action: Optional[str]
    active_alert_count: int
    median_delay_seconds: Optional[float]
    agency_key: Optional[str]
    timestamp_ms: Optional[int]
    advisories: List[str] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "route_id": self.route_id,
            "direction_id": self.direction_id,
            "label": self.label,
            "severity": self.severity,
            "severity_label": self.severity_label,
            "severity_color": self.severity_color,
            "headline": self.headline,
            "body": self.body,
            "short_summary": self.short_summary,
            "hazard_score": self.hazard_score,
            "regime": self.regime,
            "action": self.action,
            "active_alert_count": self.active_alert_count,
            "median_delay_seconds": self.median_delay_seconds,
            "agency_key": self.agency_key,
            "timestamp_ms": self.timestamp_ms,
            "advisories": self.advisories,
        }


def build_route_status(
    line: Dict[str, Any],
    regime_by_entity: Dict[str, Any],
    incidents_by_entity: Dict[str, List[Any]],
) -> RouteStatus:
    """Build a RouteStatus from a corridor snapshot and indexed regime/incident data."""
    entity_id = str(line.get("entity_id") or "")
    regime_rec = regime_by_entity.get(entity_id) or {}
    active_incidents = incidents_by_entity.get(entity_id) or []

    label = str(line.get("label") or line.get("route_id") or entity_id)
    regime = str(regime_rec.get("regime") or line.get("regime") or "")
    action = str(regime_rec.get("action") or line.get("top_action") or "")
    hazard_raw = (
        regime_rec.get("hazard_score")
        or regime_rec.get("hazard")
        or line.get("avg_hazard")
    )
    hazard: Optional[float] = float(hazard_raw) if hazard_raw is not None else None
    alert_count = int(line.get("active_alert_count") or 0)
    median_delay = line.get("median_delay_seconds")

    severity = classify_severity(regime, action, hazard, active_alert_count=alert_count)
    wording = build_wording(severity, label)

    # Build advisory list from active incidents
    advisories: List[str] = []
    for incident in active_incidents:
        summary = str(incident.get("summary") or "")
        if summary:
            advisories.append(summary)

    return RouteStatus(
        entity_id=entity_id,
        route_id=line.get("route_id"),
        direction_id=line.get("direction_id"),
        label=label,
        severity=severity,
        severity_label=SEVERITY_LABELS.get(severity, severity),
        severity_color=SEVERITY_COLOR.get(severity, "gray"),
        headline=wording["headline"],
        body=wording["body"],
        short_summary=wording["short"],
        hazard_score=hazard,
        regime=regime or None,
        action=action or None,
        active_alert_count=alert_count,
        median_delay_seconds=float(median_delay) if median_delay is not None else None,
        agency_key=line.get("agency_key"),
        timestamp_ms=int(
            line.get("timestamp_ms") or regime_rec.get("timestamp_ms") or 0
        )
        or None,
        advisories=advisories,
    )
