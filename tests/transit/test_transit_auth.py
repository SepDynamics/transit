"""Tests for auth and RBAC scaffolding."""

import json
import os
import urllib.request
from unittest import mock
from urllib.error import HTTPError

import pytest

from scripts.transit.auth import (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_VIEWER,
    TokenRegistry,
    check_auth,
    read_audit_trail,
    reload_registry,
    resolve_auth,
    role_can,
    write_audit_event,
)
from scripts.transit.api import start_transit_http_server


# ---------------------------------------------------------------------------
# role_can
# ---------------------------------------------------------------------------


def test_role_can_viewer_satisfies_viewer():
    assert role_can(ROLE_VIEWER, ROLE_VIEWER) is True


def test_role_can_operator_satisfies_viewer():
    assert role_can(ROLE_OPERATOR, ROLE_VIEWER) is True


def test_role_can_operator_satisfies_operator():
    assert role_can(ROLE_OPERATOR, ROLE_OPERATOR) is True


def test_role_can_viewer_cannot_satisfy_operator():
    assert role_can(ROLE_VIEWER, ROLE_OPERATOR) is False


def test_role_can_admin_satisfies_all():
    for role in (ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN):
        assert role_can(ROLE_ADMIN, role) is True


def test_role_can_unknown_role_fails():
    assert role_can("unknown", ROLE_VIEWER) is False


# ---------------------------------------------------------------------------
# TokenRegistry
# ---------------------------------------------------------------------------


def test_token_registry_empty_by_default():
    with mock.patch.dict(os.environ, {"TRANSIT_API_TOKENS": ""}, clear=False):
        reg = TokenRegistry()
        assert reg.enabled is False
        assert reg.resolve("anytoken") is None


def test_token_registry_loads_from_env():
    with mock.patch.dict(
        os.environ,
        {"TRANSIT_API_TOKENS": "tok1:viewer,tok2:operator,tok3:admin"},
        clear=False,
    ):
        reg = TokenRegistry()
        assert reg.enabled is True
        assert reg.resolve("tok1") == ROLE_VIEWER
        assert reg.resolve("tok2") == ROLE_OPERATOR
        assert reg.resolve("tok3") == ROLE_ADMIN
        assert reg.resolve("unknown") is None


def test_token_registry_ignores_invalid_role():
    with mock.patch.dict(
        os.environ,
        {"TRANSIT_API_TOKENS": "tok1:superuser"},
        clear=False,
    ):
        reg = TokenRegistry()
        assert reg.resolve("tok1") is None


def test_token_registry_ignores_entry_without_colon():
    with mock.patch.dict(
        os.environ,
        {"TRANSIT_API_TOKENS": "badentry,tok2:viewer"},
        clear=False,
    ):
        reg = TokenRegistry()
        assert reg.resolve("badentry") is None
        assert reg.resolve("tok2") == ROLE_VIEWER


# ---------------------------------------------------------------------------
# resolve_auth
# ---------------------------------------------------------------------------


def test_resolve_auth_missing_header():
    token, role = resolve_auth(None)
    assert token is None
    assert role is None


def test_resolve_auth_malformed_header():
    token, role = resolve_auth("Basic abc123")
    assert token is None


def test_resolve_auth_bearer_no_registry():
    # Registry is empty so token has no role
    with mock.patch.dict(os.environ, {"TRANSIT_API_TOKENS": ""}, clear=False):
        reload_registry()
        token, role = resolve_auth("Bearer mytoken")
        assert token == "mytoken"
        assert role is None


def test_resolve_auth_bearer_with_registry():
    with mock.patch.dict(
        os.environ,
        {"TRANSIT_API_TOKENS": "mytoken:operator"},
        clear=False,
    ):
        reload_registry()
        token, role = resolve_auth("Bearer mytoken")
        assert token == "mytoken"
        assert role == ROLE_OPERATOR


# ---------------------------------------------------------------------------
# check_auth
# ---------------------------------------------------------------------------


def test_check_auth_no_tokens_configured_passes():
    """When no tokens are configured and REQUIRE_AUTH is not set, all pass."""
    with mock.patch.dict(
        os.environ,
        {"TRANSIT_API_TOKENS": "", "TRANSIT_API_REQUIRE_AUTH": ""},
        clear=False,
    ):
        reload_registry()
        ok, token, role = check_auth(None)
        assert ok is True
        assert role == ROLE_VIEWER


def test_check_auth_valid_token_grants_role():
    with mock.patch.dict(
        os.environ,
        {"TRANSIT_API_TOKENS": "sec:operator", "TRANSIT_API_REQUIRE_AUTH": ""},
        clear=False,
    ):
        reload_registry()
        ok, token, role = check_auth("Bearer sec")
        assert ok is True
        assert role == ROLE_OPERATOR


def test_check_auth_insufficient_role_denied():
    with mock.patch.dict(
        os.environ,
        {"TRANSIT_API_TOKENS": "readtok:viewer", "TRANSIT_API_REQUIRE_AUTH": ""},
        clear=False,
    ):
        reload_registry()
        ok, token, role = check_auth("Bearer readtok", required_role=ROLE_OPERATOR)
        assert ok is False


def test_check_auth_unknown_token_denied_when_require_auth():
    with mock.patch.dict(
        os.environ,
        {"TRANSIT_API_TOKENS": "known:viewer", "TRANSIT_API_REQUIRE_AUTH": "1"},
        clear=False,
    ):
        reload_registry()
        ok, token, role = check_auth("Bearer unknown")
        assert ok is False


# ---------------------------------------------------------------------------
# Audit trail helpers
# ---------------------------------------------------------------------------


class _FakeClient:
    """Minimal in-memory Redis client stub for audit tests."""

    def __init__(self):
        self._lists: dict = {}

    def rpush(self, key, value):
        self._lists.setdefault(key, []).append(value)

    def ltrim(self, key, start, end):
        lst = self._lists.get(key, [])
        if end < 0:
            end = len(lst) + end + 1
        self._lists[key] = lst[start:end]

    def lrange(self, key, start, end):
        lst = self._lists.get(key, [])
        if end == -1:
            return lst[start:]
        return lst[start : end + 1]


def test_write_audit_event_stores_entry():
    client = _FakeClient()
    write_audit_event(
        client,
        action="test_action",
        token="abc123",
        role="viewer",
        resource="route:Red:0",
    )
    trail = read_audit_trail(client, limit=10)
    assert len(trail) == 1
    assert trail[0]["action"] == "test_action"
    assert trail[0]["role"] == "viewer"
    assert trail[0]["resource"] == "route:Red:0"
    # Token should be truncated for security
    assert "abc1" in trail[0]["token_prefix"]


def test_write_audit_event_no_raises_on_client_error():
    """Audit write should be silent on failure."""

    class _BrokenClient:
        def rpush(self, *_):
            raise RuntimeError("connection error")

        def ltrim(self, *_):
            pass

        def lrange(self, *_):
            return []

    # Should not raise
    write_audit_event(_BrokenClient(), action="test")


# ---------------------------------------------------------------------------
# Integration: API auth enforcement
# ---------------------------------------------------------------------------


class _FakeTransitServiceWithAck:
    """Minimal service stub that supports the ack endpoint."""

    def service_health(self):
        return {"status": "ok"}

    def transit_health(self, *, scope="all", trace_id=None):
        return {
            "scope": scope,
            "status": "ok",
            "line_count": 0,
            "active_line_count": 0,
            "scheduled_later_line_count": 0,
            "visible_line_count": 0,
        }

    def transit_entities(self, *, scope="all", trace_id=None):
        return {
            "scope": scope,
            "lines": [],
            "active_lines": [],
            "scheduled_later_lines": [],
            "inactive_lines": [],
            "vehicles": [],
        }

    def transit_regimes(self, *, scope="all", trace_id=None):
        return {"scope": scope, "regimes": [], "recurring_regimes": []}

    def transit_incidents(self, *, scope="all", trace_id=None):
        return {"scope": scope, "incidents": []}

    def transit_trends(self, *, scope="all", trace_id=None):
        return {
            "scope": scope,
            "summary": {
                "corridor_count": 0,
                "unstable_corridor_count": 0,
                "recent_incident_count": 0,
            },
            "corridors": [],
        }

    def transit_history(self, *, entity_id, scope="all", trace_id=None, limit=72):
        return {
            "scope": scope,
            "entity": {"entity_id": entity_id},
            "observations": [],
            "regimes": [],
        }

    def transit_sources(self):
        return {"scopes": [{"id": "all", "label": "All feeds"}]}

    def transit_map(self, *, scope="all", trace_id=None):
        return {
            "type": "FeatureCollection",
            "scope": scope,
            "vehicle_features": [],
            "corridor_summaries": [],
            "vehicle_count": 0,
            "corridor_count": 0,
        }

    def transit_scorecard(self, *, scope="all", trace_id=None, limit=720):
        return {
            "scope": scope,
            "window_snapshots": 0,
            "corridor_count": 0,
            "total_incidents": 0,
            "network": {
                "avg_hazard": 0,
                "avg_delay_seconds": 0,
                "on_time_pct": 100.0,
                "unstable_corridor_count": 0,
                "top_regimes": {},
                "top_actions": {},
            },
            "corridors": [],
        }

    def public_status_routes(self, *, scope="live", trace_id=None):
        return {"scope": scope, "route_count": 0, "routes": []}

    def public_status_network(self, *, scope="live", trace_id=None):
        return {
            "scope": scope,
            "severity": "good",
            "severity_label": "Good Service",
            "severity_color": "green",
            "active_route_count": 0,
            "incident_count": 0,
            "critical_incident_count": 0,
            "disrupted_route_count": 0,
            "disrupted_routes": [],
        }

    def public_status_alerts(self, *, scope="live", trace_id=None):
        return {"scope": scope, "alert_count": 0, "alerts": []}

    def public_status_scorecard(self, *, scope="live", trace_id=None, limit=720):
        return {
            "scope": scope,
            "window_snapshots": 0,
            "corridor_count": 0,
            "total_incidents": 0,
            "network": {
                "on_time_pct": 100.0,
                "avg_delay_seconds": 0,
                "unstable_corridor_count": 0,
            },
            "corridors": [],
        }

    def acknowledge_incident(self, incident_id, *, note="", acknowledged_by=None):
        return {
            "acknowledged": True,
            "incident_id": incident_id,
            "note": note,
            "acknowledged_by": acknowledged_by or "",
            "acknowledged_at": "2024-01-01T00:00:00Z",
        }

    def audit_trail(self, *, limit=100):
        return {"generated_at": "2024-01-01T00:00:00Z", "event_count": 0, "events": []}

    # store attribute for write_audit_event
    class _FakeStore:
        class client:
            @staticmethod
            def rpush(*_):
                pass

            @staticmethod
            def ltrim(*_):
                pass

    store = _FakeStore()


def _post_json(url: str, payload: dict, token: str | None = None):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Content-Length", str(len(body)))
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_public_status_endpoints_open_without_auth():
    """Public /api/status/* endpoints should work without any token."""
    # Ensure auth is not required
    with mock.patch.dict(
        os.environ,
        {"TRANSIT_API_TOKENS": "sec:operator", "TRANSIT_API_REQUIRE_AUTH": ""},
        clear=False,
    ):
        reload_registry()
        server = start_transit_http_server(
            _FakeTransitServiceWithAck(), host="127.0.0.1", port=0
        )
        try:
            url = f"http://127.0.0.1:{server.server_port}/api/status/network"
            with urllib.request.urlopen(url) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
    assert payload["severity"] == "good"


def test_ops_endpoints_open_when_no_tokens_configured():
    """When no tokens configured and REQUIRE_AUTH not set, ops endpoints open."""
    with mock.patch.dict(
        os.environ,
        {"TRANSIT_API_TOKENS": "", "TRANSIT_API_REQUIRE_AUTH": ""},
        clear=False,
    ):
        reload_registry()
        server = start_transit_http_server(
            _FakeTransitServiceWithAck(), host="127.0.0.1", port=0
        )
        try:
            url = f"http://127.0.0.1:{server.server_port}/api/transit/health"
            with urllib.request.urlopen(url) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
    assert payload["status"] == "ok"


def test_incident_ack_endpoint_requires_operator_role():
    """POST /api/transit/incidents/ack should reject viewer-role tokens."""
    with mock.patch.dict(
        os.environ,
        {
            "TRANSIT_API_TOKENS": "readtok:viewer,optok:operator",
            "TRANSIT_API_REQUIRE_AUTH": "",
        },
        clear=False,
    ):
        reload_registry()
        server = start_transit_http_server(
            _FakeTransitServiceWithAck(), host="127.0.0.1", port=0
        )
        try:
            url = f"http://127.0.0.1:{server.server_port}/api/transit/incidents/ack"
            # viewer token — should be rejected
            status, payload = _post_json(
                url, {"incident_id": "inc-001"}, token="readtok"
            )
            assert status == 401

            # operator token — should succeed
            status2, payload2 = _post_json(
                url, {"incident_id": "inc-001"}, token="optok"
            )
            assert status2 == 200
            assert payload2["acknowledged"] is True
            assert payload2["incident_id"] == "inc-001"
        finally:
            server.shutdown()
            server.server_close()


def test_incident_ack_missing_incident_id_returns_400():
    with mock.patch.dict(
        os.environ,
        {"TRANSIT_API_TOKENS": "optok:operator", "TRANSIT_API_REQUIRE_AUTH": ""},
        clear=False,
    ):
        reload_registry()
        server = start_transit_http_server(
            _FakeTransitServiceWithAck(), host="127.0.0.1", port=0
        )
        try:
            url = f"http://127.0.0.1:{server.server_port}/api/transit/incidents/ack"
            status, payload = _post_json(url, {"note": "oops"}, token="optok")
            assert status == 400
            assert payload["error"] == "missing_incident_id"
        finally:
            server.shutdown()
            server.server_close()
