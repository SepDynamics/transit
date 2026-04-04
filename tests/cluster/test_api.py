import json
from urllib.request import urlopen

from scripts.cluster.api import start_http_server


class _FakeClusterService:
    def service_health(self):
        return {"service": "Cluster Sentinel API", "status": "ok"}

    def cluster_health(self, *, scope: str = "all", trace_id=None):
        return {"scope": scope, "trace_id": trace_id, "status": "ok", "entity_count": 1}

    def gpu_inventory(self, *, scope: str = "all", trace_id=None):
        return {"scope": scope, "trace_id": trace_id, "nodes": [], "gpus": []}

    def latest_regimes(self, *, scope: str = "all", trace_id=None):
        return {"scope": scope, "trace_id": trace_id, "regimes": [], "recurring_signatures": []}

    def incidents(self, *, scope: str = "all", trace_id=None):
        return {"scope": scope, "trace_id": trace_id, "incidents": []}

    def history(self, *, host: str, gpu_index: int, scope: str = "all", trace_id=None, limit: int = 120):
        return {
            "scope": scope,
            "trace_id": trace_id,
            "entity": {"host": host, "gpu_index": gpu_index},
            "telemetry": [],
            "regimes": [],
        }

    def sources(self):
        return {"scopes": [{"id": "all", "label": "All streams"}]}


def test_cluster_api_health_endpoint_serves_json():
    server = start_http_server(_FakeClusterService(), host="127.0.0.1", port=0)
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/cluster/health?scope=live&trace_id=trace-123") as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert payload["scope"] == "live"
    assert payload["trace_id"] == "trace-123"
    assert payload["status"] == "ok"
    assert payload["entity_count"] == 1
