"""Tests for the public service-status severity rules and wording templates."""

import pytest

from scripts.transit.severity import (
    SEVERITY_ADVISORY,
    SEVERITY_DELAY,
    SEVERITY_DISRUPTION,
    SEVERITY_GOOD,
    SEVERITY_SEVERE,
    SEVERITY_UNKNOWN,
    build_route_status,
    build_wording,
    classify_network_severity,
    classify_severity,
    severity_rank,
)


# ---------------------------------------------------------------------------
# classify_severity
# ---------------------------------------------------------------------------


def test_classify_severity_healthy_regime_is_good():
    assert classify_severity("healthy", "monitor", 0.05) == SEVERITY_GOOD


def test_classify_severity_recovering_regime_is_advisory():
    assert classify_severity("recovering", "monitor", 0.15) == SEVERITY_ADVISORY


def test_classify_severity_corridor_unstable_is_delay():
    assert classify_severity("corridor_unstable", "hold", 0.55) == SEVERITY_DELAY


def test_classify_severity_headway_collapse_is_disruption():
    assert (
        classify_severity("headway_collapse", "dispatch_relief", 0.72)
        == SEVERITY_DISRUPTION
    )


def test_classify_severity_terminal_blocked_is_severe():
    assert classify_severity("terminal_blocked", "short_turn", 0.91) == SEVERITY_SEVERE


def test_classify_severity_action_escalates_severity():
    # monitor + healthy regime → good, but dispatch_relief upgrades to disruption
    result = classify_severity("healthy", "dispatch_relief", 0.05)
    assert result == SEVERITY_DISRUPTION


def test_classify_severity_high_hazard_escalates():
    # No regime, but hazard alone pushes to severe
    result = classify_severity(None, None, 0.9)
    assert result == SEVERITY_SEVERE


def test_classify_severity_moderate_hazard_gives_delay():
    result = classify_severity(None, None, 0.5)
    assert result == SEVERITY_DELAY


def test_classify_severity_low_hazard_gives_advisory():
    result = classify_severity(None, None, 0.22)
    assert result == SEVERITY_ADVISORY


def test_classify_severity_minimal_hazard_gives_good():
    result = classify_severity(None, None, 0.05)
    assert result == SEVERITY_GOOD


def test_classify_severity_alerts_escalate_to_advisory():
    # Healthy regime but alerts present → advisory
    result = classify_severity("healthy", "monitor", 0.05, active_alert_count=1)
    assert result == SEVERITY_ADVISORY


def test_classify_severity_many_alerts_escalate_to_delay():
    result = classify_severity("healthy", "monitor", 0.05, active_alert_count=3)
    assert result == SEVERITY_DELAY


def test_classify_severity_unknown_regime_not_misleading():
    # An unmapped regime string should not incorrectly force a known tier
    result = classify_severity("totally_new_regime", "monitor", 0.0)
    # With zero hazard, no alerts, and no matching action, result is good
    assert result == SEVERITY_GOOD


def test_classify_severity_none_inputs_default_to_good():
    assert classify_severity(None, None, None) == SEVERITY_GOOD


# ---------------------------------------------------------------------------
# severity_rank
# ---------------------------------------------------------------------------


def test_severity_rank_ordering():
    assert severity_rank(SEVERITY_GOOD) < severity_rank(SEVERITY_ADVISORY)
    assert severity_rank(SEVERITY_ADVISORY) < severity_rank(SEVERITY_DELAY)
    assert severity_rank(SEVERITY_DELAY) < severity_rank(SEVERITY_DISRUPTION)
    assert severity_rank(SEVERITY_DISRUPTION) < severity_rank(SEVERITY_SEVERE)


def test_severity_rank_unknown_returns_negative():
    assert severity_rank("not_a_tier") == -1


# ---------------------------------------------------------------------------
# build_wording
# ---------------------------------------------------------------------------


def test_build_wording_good_contains_route():
    wording = build_wording(SEVERITY_GOOD, "Red Line")
    assert "Red Line" in wording["headline"]
    assert "Red Line" in wording["body"]
    assert wording["short"] == "Normal service"


def test_build_wording_severe_contains_alternate_routes_advice():
    wording = build_wording(SEVERITY_SEVERE, "Orange Line")
    assert "Orange Line" in wording["body"]
    assert "alternate" in wording["body"].lower()


def test_build_wording_unknown_severity_uses_fallback():
    wording = build_wording("not_a_tier", "Blue Line")
    assert "Blue Line" in wording["headline"]
    assert wording["short"] == "Status unavailable"


def test_build_wording_all_tiers_render_without_error():
    from scripts.transit.severity import SEVERITY_ORDER

    for tier in SEVERITY_ORDER:
        wording = build_wording(tier, "Test Route")
        assert wording["headline"]
        assert wording["body"]
        assert wording["short"]


# ---------------------------------------------------------------------------
# classify_network_severity
# ---------------------------------------------------------------------------


def test_classify_network_severity_empty_returns_good():
    assert classify_network_severity([]) == SEVERITY_GOOD


def test_classify_network_severity_all_good():
    assert classify_network_severity([SEVERITY_GOOD, SEVERITY_GOOD]) == SEVERITY_GOOD


def test_classify_network_severity_worst_wins():
    severities = [SEVERITY_GOOD, SEVERITY_DELAY, SEVERITY_ADVISORY, SEVERITY_DISRUPTION]
    assert classify_network_severity(severities) == SEVERITY_DISRUPTION


def test_classify_network_severity_single_severe():
    assert classify_network_severity([SEVERITY_SEVERE]) == SEVERITY_SEVERE


# ---------------------------------------------------------------------------
# build_route_status
# ---------------------------------------------------------------------------


def _make_line(**kwargs):
    defaults = {
        "entity_id": "route:Red:0",
        "route_id": "Red",
        "direction_id": 0,
        "label": "Red Line",
        "avg_hazard": 0.0,
        "median_delay_seconds": 30,
        "active_alert_count": 0,
        "activity_status": "active",
        "agency_key": "mbta",
        "timestamp_ms": 1700000000000,
    }
    defaults.update(kwargs)
    return defaults


def test_build_route_status_healthy_corridor():
    line = _make_line()
    regime_by = {}
    incidents_by = {}
    status = build_route_status(line, regime_by, incidents_by)
    assert status.severity == SEVERITY_GOOD
    assert status.entity_id == "route:Red:0"
    assert status.label == "Red Line"
    assert "Red Line" in status.headline


def test_build_route_status_bunching_onset_is_delay():
    line = _make_line(avg_hazard=0.55)
    regime_by = {
        "route:Red:0": {
            "entity_id": "route:Red:0",
            "regime": "bunching_onset",
            "action": "hold",
            "hazard": 0.55,
        }
    }
    status = build_route_status(line, regime_by, {})
    assert status.severity == SEVERITY_DELAY
    assert status.regime == "bunching_onset"


def test_build_route_status_advisories_from_incidents():
    line = _make_line()
    regime_by = {
        "route:Red:0": {"entity_id": "route:Red:0", "regime": "corridor_unstable"}
    }
    incidents_by = {
        "route:Red:0": [
            {"incident_id": "inc-1", "summary": "Train bunching near Park Street"},
        ]
    }
    status = build_route_status(line, regime_by, incidents_by)
    assert "Train bunching near Park Street" in status.advisories


def test_build_route_status_to_json_has_required_keys():
    line = _make_line()
    status = build_route_status(line, {}, {})
    record = status.to_json()
    required_keys = {
        "entity_id",
        "label",
        "severity",
        "severity_label",
        "severity_color",
        "headline",
        "body",
        "short_summary",
        "advisories",
    }
    for key in required_keys:
        assert key in record, f"Missing key: {key}"


def test_build_route_status_terminal_blocked_is_severe():
    line = _make_line(avg_hazard=0.91)
    regime_by = {
        "route:Red:0": {
            "entity_id": "route:Red:0",
            "regime": "terminal_blocked",
            "action": "short_turn",
            "hazard": 0.91,
        }
    }
    status = build_route_status(line, regime_by, {})
    assert status.severity == SEVERITY_SEVERE
