import json
from urllib.error import HTTPError
from urllib.request import urlopen

from scripts.transit.api import start_transit_http_server


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
        return {"scope": scope, "trace_id": trace_id, "regimes": [], "recurring_regimes": []}

    def transit_incidents(self, *, scope: str = "all", trace_id=None):
        return {"scope": scope, "trace_id": trace_id, "incidents": []}

    def transit_trends(self, *, scope: str = "all", trace_id=None):
        return {
            "scope": scope,
            "trace_id": trace_id,
            "summary": {"corridor_count": 1, "unstable_corridor_count": 1, "recent_incident_count": 2},
            "corridors": [{"entity_id": "route:Red:0", "label": "Red Line", "latest_hazard": 0.81}],
        }

    def transit_history(self, *, entity_id: str, scope: str = "all", trace_id=None, limit: int = 72):
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
                    "properties": {"entity_id": "vehicle:1811", "regime": "bunching_onset"},
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


def test_transit_api_health_endpoint_serves_json():
    server = start_transit_http_server(_FakeTransitService(), host="127.0.0.1", port=0)
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/transit/health?scope=live") as response:
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
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/transit/trends?scope=live") as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert payload["summary"]["corridor_count"] == 1
    assert payload["summary"]["recent_incident_count"] == 2
    assert payload["corridors"][0]["entity_id"] == "route:Red:0"


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
            urlopen(f"http://127.0.0.1:{server.server_port}/api/transit/history?scope=live")
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
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/transit/map?scope=replay&trace_id=trace-123") as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert payload["scope"] == "replay"
    assert payload["trace_id"] == "trace-123"
    assert payload["vehicle_count"] == 1
    assert payload["vehicle_features"][0]["properties"]["entity_id"] == "vehicle:1811"


def test_transit_api_scorecard_endpoint_serves_json():
    server = start_transit_http_server(_FakeTransitService(), host="127.0.0.1", port=0)
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/transit/scorecard?scope=live&limit=144") as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert payload["scope"] == "live"
    assert payload["window_snapshots"] == 144
    assert payload["network"]["on_time_pct"] == 50.0
    assert payload["corridors"][0]["entity_id"] == "route:Red:0"
