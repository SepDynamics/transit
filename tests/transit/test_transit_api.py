import json
from urllib.error import HTTPError
from urllib.request import urlopen

from scripts.transit.api import TransitAPIService, start_transit_http_server


class _FakeTransitService:
    def service_health(self):
        return {"service": "Transit Sentinel API", "status": "ok"}

    def transit_health(self, *, scope: str = "all", trace_id=None):
        return {
            "scope": scope,
            "trace_id": trace_id,
            "status": "ok",
            "line_count": 2,
            "active_line_count": 2,
            "scheduled_later_line_count": 1,
            "visible_line_count": 3,
        }

    def transit_entities(self, *, scope: str = "all", trace_id=None):
        return {
            "scope": scope,
            "trace_id": trace_id,
            "lines": [],
            "active_lines": [],
            "scheduled_later_lines": [],
            "inactive_lines": [],
            "vehicles": [],
        }

    def transit_regimes(self, *, scope: str = "all", trace_id=None):
        return {
            "scope": scope,
            "trace_id": trace_id,
            "regimes": [],
            "recurring_regimes": [],
        }

    def transit_incidents(self, *, scope: str = "all", trace_id=None):
        return {"scope": scope, "trace_id": trace_id, "incidents": []}

    def transit_trends(self, *, scope: str = "all", trace_id=None):
        return {
            "scope": scope,
            "trace_id": trace_id,
            "summary": {
                "corridor_count": 1,
                "unstable_corridor_count": 1,
                "recent_incident_count": 2,
            },
            "corridors": [
                {"entity_id": "route:Red:0", "label": "Red Line", "latest_hazard": 0.81}
            ],
        }

    def transit_dashboard(self, *, scope: str = "all", trace_id=None):
        return {
            "scope": scope,
            "trace_id": trace_id,
            "health": self.transit_health(scope=scope, trace_id=trace_id),
            "entities": self.transit_entities(scope=scope, trace_id=trace_id),
            "regimes": self.transit_regimes(scope=scope, trace_id=trace_id),
            "incidents": self.transit_incidents(scope=scope, trace_id=trace_id),
            "trends": self.transit_trends(scope=scope, trace_id=trace_id),
        }

    def transit_history(
        self, *, entity_id: str, scope: str = "all", trace_id=None, limit: int = 72
    ):
        return {
            "scope": scope,
            "trace_id": trace_id,
            "entity": {"entity_id": entity_id},
            "observations": [],
            "regimes": [],
        }

    def transit_sources(self):
        return {"scopes": [{"id": "all", "label": "All feeds"}]}

    def transit_map(self, *, scope: str = "all", trace_id=None):
        return {
            "type": "FeatureCollection",
            "scope": scope,
            "trace_id": trace_id,
            "vehicle_features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-71.06, 42.36]},
                    "properties": {
                        "entity_id": "vehicle:1811",
                        "regime": "bunching_onset",
                    },
                }
            ],
            "corridor_features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[-71.14, 42.395], [-71.12, 42.396]],
                    },
                    "properties": {
                        "entity_id": "route:Red:0",
                        "label": "Red Line",
                        "regime": "bunching_onset",
                    },
                }
            ],
            "corridor_summaries": [{"entity_id": "route:Red:0", "label": "Red Line"}],
            "vehicle_count": 1,
            "corridor_count": 1,
        }

    def transit_scorecard(self, *, scope: str = "all", trace_id=None, limit: int = 720):
        return {
            "scope": scope,
            "trace_id": trace_id,
            "window_snapshots": limit,
            "corridor_count": 1,
            "total_incidents": 2,
            "network": {
                "avg_hazard": 0.61,
                "avg_delay_seconds": 165,
                "on_time_pct": 50.0,
                "unstable_corridor_count": 1,
                "top_regimes": {"bunching_onset": 1},
                "top_actions": {"hold": 1},
            },
            "corridors": [
                {
                    "entity_id": "route:Red:0",
                    "label": "Red Line",
                    "avg_hazard": 0.61,
                    "hazard_p90": 0.81,
                    "avg_delay_seconds": 165,
                    "on_time_pct": 50.0,
                    "incident_count": 2,
                    "snapshot_count": 2,
                    "healthy_pct": 0.0,
                    "unstable_pct": 100.0,
                    "top_regime": "bunching_onset",
                    "top_action": "hold",
                    "max_hazard": 0.81,
                    "max_delay_seconds": 240,
                    "regime_counts": {"bunching_onset": 1},
                    "action_counts": {"hold": 1},
                }
            ],
        }

    def public_status_routes(self, *, scope: str = "live", trace_id=None):
        return {
            "generated_at": "2024-01-01T00:00:00.000Z",
            "scope": scope,
            "route_count": 2,
            "routes": [
                {
                    "entity_id": "route:Red:0",
                    "route_id": "Red",
                    "direction_id": 0,
                    "label": "Red Line",
                    "severity": "delay",
                    "severity_label": "Delays",
                    "severity_color": "orange",
                    "headline": "Red Line: Delays",
                    "body": "Delays are reported on Red Line.",
                    "short_summary": "Delays reported",
                    "hazard_score": 0.61,
                    "regime": "bunching_onset",
                    "action": "hold",
                    "active_alert_count": 1,
                    "median_delay_seconds": 165.0,
                    "agency_key": "mbta",
                    "timestamp_ms": 1700000000000,
                    "advisories": ["Minor bunching detected"],
                },
                {
                    "entity_id": "route:Green-B:0",
                    "route_id": "Green-B",
                    "direction_id": 0,
                    "label": "Green Line B",
                    "severity": "good",
                    "severity_label": "Good Service",
                    "severity_color": "green",
                    "headline": "Green Line B: Good service",
                    "body": "Green Line B is operating normally.",
                    "short_summary": "Normal service",
                    "hazard_score": 0.08,
                    "regime": "healthy",
                    "action": "monitor",
                    "active_alert_count": 0,
                    "median_delay_seconds": 15.0,
                    "agency_key": "mbta",
                    "timestamp_ms": 1700000000000,
                    "advisories": [],
                },
            ],
        }

    def public_status_network(self, *, scope: str = "live", trace_id=None):
        return {
            "generated_at": "2024-01-01T00:00:00.000Z",
            "scope": scope,
            "severity": "delay",
            "severity_label": "Delays",
            "severity_color": "orange",
            "active_route_count": 2,
            "incident_count": 2,
            "critical_incident_count": 0,
            "disrupted_route_count": 1,
            "disrupted_routes": [
                {"entity_id": "route:Red:0", "label": "Red Line", "severity": "delay"}
            ],
            "feed_status": {"collection_source": "gtfs_rt"},
        }

    def public_status_alerts(self, *, scope: str = "live", trace_id=None):
        return {
            "generated_at": "2024-01-01T00:00:00.000Z",
            "scope": scope,
            "alert_count": 1,
            "alerts": [
                {
                    "alert_id": "inc-001",
                    "entity_id": "route:Red:0",
                    "route_label": "Red Line",
                    "severity": "delay",
                    "severity_label": "Delays",
                    "severity_color": "orange",
                    "headline": "Minor bunching detected on Red Line.",
                    "recommended_action": "Allow extra travel time.",
                    "timestamp_ms": 1700000000000,
                }
            ],
        }

    def public_status_scorecard(
        self, *, scope: str = "live", trace_id=None, limit: int = 720
    ):
        return {
            "generated_at": "2024-01-01T00:00:00.000Z",
            "scope": scope,
            "window_snapshots": limit,
            "corridor_count": 1,
            "total_incidents": 2,
            "network": {
                "on_time_pct": 50.0,
                "avg_delay_seconds": 165,
                "unstable_corridor_count": 1,
            },
            "corridors": [
                {
                    "entity_id": "route:Red:0",
                    "label": "Red Line",
                    "on_time_pct": 50.0,
                    "avg_delay_seconds": 165,
                    "incident_count": 2,
                    "snapshot_count": 2,
                    "healthy_pct": 0.0,
                    "unstable_pct": 100.0,
                }
            ],
        }


class _ReadModelStore:
    def __init__(self):
        self.scorecard_calls = 0
        self.trends_calls = 0
        self.dashboard_calls = 0

    def read_live_read_model(self, kind):
        if kind == "scorecard":
            return {
                "generated_at": "2024-01-01T00:00:00.000Z",
                "scope": "live",
                "window_snapshots": 60,
                "network": {"on_time_pct": 99.0},
                "corridors": [],
                "read_model": {
                    "kind": "scorecard",
                    "scope": "live",
                    "generated_at": "2024-01-01T00:00:00.000Z",
                    "limit": 60,
                },
            }
        if kind == "trends":
            return {
                "generated_at": "2024-01-01T00:00:00.000Z",
                "scope": "live",
                "summary": {"corridor_count": 0},
                "corridors": [],
                "read_model": {
                    "kind": "trends",
                    "scope": "live",
                    "generated_at": "2024-01-01T00:00:00.000Z",
                    "limit": 6,
                    "window": 24,
                },
            }
        if kind == "dashboard":
            return {
                "generated_at": "2024-01-01T00:00:00.000Z",
                "scope": "live",
                "health": {"status": "ok"},
                "entities": {"vehicles": []},
                "regimes": {"regimes": []},
                "incidents": {"incidents": []},
                "trends": {"summary": {"corridor_count": 0}, "corridors": []},
                "read_model": {
                    "kind": "dashboard",
                    "scope": "live",
                    "generated_at": "2024-01-01T00:00:00.000Z",
                },
            }
        if kind == "status:network":
            return {
                "generated_at": "2024-01-01T00:00:00.000Z",
                "scope": "live",
                "severity": "good",
                "severity_label": "Good Service",
                "severity_color": "green",
                "active_route_count": 1,
                "incident_count": 0,
                "critical_incident_count": 0,
                "disrupted_route_count": 0,
                "disrupted_routes": [],
                "read_model": {
                    "kind": "status:network",
                    "scope": "live",
                    "generated_at": "2024-01-01T00:00:00.000Z",
                },
            }
        return {}

    def scorecard(self, **_kwargs):
        self.scorecard_calls += 1
        return {
            "generated_at": "fallback",
            "scope": "live",
            "window_snapshots": 10,
            "network": {"on_time_pct": 50.0},
            "corridors": [],
        }

    def trends(self, **_kwargs):
        self.trends_calls += 1
        return {"generated_at": "fallback", "summary": {}, "corridors": []}

    def health(self, **_kwargs):
        self.dashboard_calls += 1
        return {"status": "fallback"}


def test_transit_api_health_endpoint_serves_json():
    server = start_transit_http_server(_FakeTransitService(), host="127.0.0.1", port=0)
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/api/transit/health?scope=live"
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert payload["scope"] == "live"
    assert payload["status"] == "ok"
    assert payload["line_count"] == 2
    assert payload["scheduled_later_line_count"] == 1


def test_transit_api_trends_endpoint_serves_json():
    server = start_transit_http_server(_FakeTransitService(), host="127.0.0.1", port=0)
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/api/transit/trends?scope=live"
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert payload["summary"]["corridor_count"] == 1
    assert payload["summary"]["recent_incident_count"] == 2
    assert payload["corridors"][0]["entity_id"] == "route:Red:0"


def test_transit_api_dashboard_endpoint_serves_json():
    server = start_transit_http_server(_FakeTransitService(), host="127.0.0.1", port=0)
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/api/transit/dashboard?scope=live"
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert payload["scope"] == "live"
    assert payload["health"]["status"] == "ok"
    assert payload["entities"]["vehicles"] == []
    assert payload["trends"]["summary"]["corridor_count"] == 1


def test_transit_api_history_endpoint_serves_json():
    server = start_transit_http_server(_FakeTransitService(), host="127.0.0.1", port=0)
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/api/transit/history?scope=replay&trace_id=trace-123&entity_id=vehicle%3A1811&limit=24"
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert payload["scope"] == "replay"
    assert payload["trace_id"] == "trace-123"
    assert payload["entity"]["entity_id"] == "vehicle:1811"


def test_transit_api_history_endpoint_requires_entity_id():
    server = start_transit_http_server(_FakeTransitService(), host="127.0.0.1", port=0)
    try:
        try:
            urlopen(
                f"http://127.0.0.1:{server.server_port}/api/transit/history?scope=live"
            )
        except HTTPError as exc:
            assert exc.code == 400
            payload = json.loads(exc.read().decode("utf-8"))
        else:  # pragma: no cover - defensive check
            raise AssertionError("expected HTTPError for missing entity_id")
    finally:
        server.shutdown()
        server.server_close()

    assert payload == {"error": "missing_entity_id"}


def test_transit_api_map_endpoint_serves_json():
    server = start_transit_http_server(_FakeTransitService(), host="127.0.0.1", port=0)
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/api/transit/map?scope=replay&trace_id=trace-123"
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert payload["scope"] == "replay"
    assert payload["trace_id"] == "trace-123"
    assert payload["vehicle_count"] == 1
    assert payload["vehicle_features"][0]["properties"]["entity_id"] == "vehicle:1811"
    assert payload["corridor_features"][0]["geometry"]["type"] == "LineString"


def test_transit_api_service_map_joins_vehicle_regime_and_corridor_geometry():
    class _FakeStore:
        def entities(self, *, scope="all", trace_id=None):
            return {
                "scope": scope,
                "trace_id": trace_id,
                "vehicles": [
                    {
                        "entity_id": "vehicle:1811",
                        "route_id": "Red",
                        "corridor_id": "corridor:mbta:Red:0",
                        "corridor_entity_id": "route:Red:0",
                        "route_label": "Red Line",
                        "observation": {
                            "vehicle_id": "1811",
                            "route_id": "Red",
                            "direction_id": 0,
                            "latitude": 42.396,
                            "longitude": -71.122,
                            "delay_seconds": 240,
                            "current_status": "IN_TRANSIT_TO",
                        },
                    }
                ],
                "active_lines": [
                    {
                        "entity_id": "route:Red:0",
                        "route_id": "Red",
                        "direction_id": 0,
                        "label": "Red Line",
                        "vehicle_count": 1,
                        "top_action": "hold",
                        "avg_hazard": 0.82,
                        "activity_status": "active_now",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[-71.14, 42.395], [-71.12, 42.396]],
                        },
                    }
                ],
                "scheduled_later_lines": [],
                "inactive_lines": [],
                "lines": [],
            }

        def regimes(self, *, scope="all", trace_id=None):
            return {
                "scope": scope,
                "trace_id": trace_id,
                "regimes": [
                    {
                        "entity_id": "route:Red:0",
                        "regime": "headway_collapse",
                        "hazard_score": 0.91,
                        "action": "dispatch_relief",
                        "timestamp_ms": 1700000000000,
                    }
                ],
            }

        def incidents(self, *, scope="all", trace_id=None):
            return {
                "scope": scope,
                "trace_id": trace_id,
                "incidents": [
                    {"entity_id": "route:Red:0", "incident_id": "inc-1"}
                ],
            }

    service = TransitAPIService("redis://unused", store=_FakeStore())

    payload = service.transit_map(scope="live")

    assert payload["vehicle_features"][0]["properties"]["regime"] == "headway_collapse"
    assert payload["vehicle_features"][0]["properties"]["corridor_entity_id"] == "route:Red:0"
    assert payload["corridor_features"][0]["geometry"]["type"] == "LineString"
    assert payload["corridor_summaries"][0]["incident_count"] == 1


def test_transit_api_scorecard_endpoint_serves_json():
    server = start_transit_http_server(_FakeTransitService(), host="127.0.0.1", port=0)
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/api/transit/scorecard?scope=live&limit=144"
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert payload["scope"] == "live"
    assert payload["window_snapshots"] == 144
    assert payload["network"]["on_time_pct"] == 50.0
    assert payload["corridors"][0]["entity_id"] == "route:Red:0"


def test_public_status_routes_endpoint_serves_json():
    server = start_transit_http_server(_FakeTransitService(), host="127.0.0.1", port=0)
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/api/status/routes"
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert payload["scope"] == "live"
    assert payload["route_count"] == 2
    assert payload["routes"][0]["entity_id"] == "route:Red:0"
    assert payload["routes"][0]["severity"] == "delay"
    assert "headline" in payload["routes"][0]
    assert "advisories" in payload["routes"][0]


def test_public_status_network_endpoint_serves_json():
    server = start_transit_http_server(_FakeTransitService(), host="127.0.0.1", port=0)
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/api/status/network"
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert payload["scope"] == "live"
    assert payload["severity"] == "delay"
    assert payload["severity_label"] == "Delays"
    assert payload["disrupted_route_count"] == 1
    assert payload["disrupted_routes"][0]["entity_id"] == "route:Red:0"


def test_public_status_alerts_endpoint_serves_json():
    server = start_transit_http_server(_FakeTransitService(), host="127.0.0.1", port=0)
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/api/status/alerts"
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert payload["scope"] == "live"
    assert payload["alert_count"] == 1
    assert payload["alerts"][0]["alert_id"] == "inc-001"
    assert payload["alerts"][0]["severity"] == "delay"
    assert payload["alerts"][0]["route_label"] == "Red Line"


def test_public_status_scorecard_endpoint_serves_json():
    server = start_transit_http_server(_FakeTransitService(), host="127.0.0.1", port=0)
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/api/status/scorecard?limit=144"
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert payload["scope"] == "live"
    assert payload["window_snapshots"] == 144
    assert payload["network"]["on_time_pct"] == 50.0
    assert payload["corridors"][0]["entity_id"] == "route:Red:0"
    # Internal vocab (regime/action counts) should not be on the public scorecard
    assert "top_regime" not in payload["corridors"][0]
    assert "regime_counts" not in payload["corridors"][0]


def test_transit_api_service_uses_live_read_models_for_default_frontend_paths():
    store = _ReadModelStore()
    service = TransitAPIService("redis://unused", store=store)

    scorecard = service.transit_scorecard(scope="live", limit=60)
    trends = service.transit_trends(scope="live")
    dashboard = service.transit_dashboard(scope="live")
    network = service.public_status_network(scope="live")

    assert scorecard["read_model"]["kind"] == "scorecard"
    assert scorecard["network"]["on_time_pct"] == 99.0
    assert trends["read_model"]["kind"] == "trends"
    assert dashboard["read_model"]["kind"] == "dashboard"
    assert network["read_model"]["kind"] == "status:network"
    assert store.scorecard_calls == 0
    assert store.trends_calls == 0
    assert store.dashboard_calls == 0


def test_transit_api_service_skips_scorecard_read_model_when_limit_differs():
    store = _ReadModelStore()
    service = TransitAPIService("redis://unused", store=store)

    payload = service.transit_scorecard(scope="live", limit=10)

    assert payload["generated_at"] == "fallback"
    assert store.scorecard_calls == 1
