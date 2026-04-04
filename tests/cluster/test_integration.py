import json
from urllib.parse import urlencode
from urllib.request import urlopen

from scripts.cluster.api import ClusterAPIService, start_http_server
from scripts.cluster.models import TelemetrySample
from scripts.cluster.policy_engine import PolicyConfig, PolicyEngineService
from scripts.cluster.regime_service import RegimeService, RegimeServiceConfig
from scripts.cluster.replay import build_demo_trace, replay_trace
from scripts.cluster.storage import ClusterStore
from scripts.cluster.telemetry_collector import CollectorConfig, TelemetryCollectorService


class _SequencedSource:
    name = "test-sequence"

    def __init__(self, frames):
        self._frames = list(frames)
        self._index = 0

    def collect(self, *, timestamp_ms: int):
        frame = self._frames[self._index] if self._index < len(self._frames) else self._frames[-1]
        self._index += 1
        rows = []
        for sample in frame:
            payload = sample.to_json()
            payload["timestamp_ms"] = timestamp_ms + (self._index * 1000)
            rows.append(TelemetrySample.from_mapping(payload))
        return rows


def test_end_to_end_runtime_paths_with_temporary_valkey(valkey_url):
    collector = TelemetryCollectorService(
        CollectorConfig(
            redis_url=valkey_url,
            interval_seconds=0.1,
            sample_retention=120,
            preferred_source="auto",
            host="live-node-a",
            dcgm_urls=[],
        )
    )
    collector._source = _SequencedSource([[_live_memory_pressure_sample(index)] for index in range(14)])
    for _ in range(14):
        collector.run_once()

    store = ClusterStore(valkey_url)
    replay_trace(
        store,
        build_demo_trace(scenario="mixed"),
        trace_id="integration-demo-trace",
        scenario="none",
        speed=1_000_000.0,
        host_prefix="itest",
        retention=120,
    )

    regime = RegimeService(
        RegimeServiceConfig(
            redis_url=valkey_url,
            window_samples=12,
            loop_seconds=1.0,
            history_retention=120,
            signature_retention_minutes=60,
        )
    )
    emitted = regime.run_once()
    assert len(emitted) >= 2

    policy = PolicyEngineService(
        PolicyConfig(
            redis_url=valkey_url,
            loop_seconds=1.0,
            stale_after_seconds=300,
            cluster_name="Integration Test Cluster",
        )
    )
    policy.run_once()

    api_service = ClusterAPIService(
        valkey_url,
        cluster_name="Integration Test Cluster",
        stale_after_seconds=300,
    )
    server = start_http_server(api_service, host="127.0.0.1", port=0)
    try:
        all_health = fetch_json(server.server_port, "/api/cluster/health", scope="all")
        live_health = fetch_json(server.server_port, "/api/cluster/health", scope="live")
        replay_health = fetch_json(server.server_port, "/api/cluster/health", scope="replay")
        inventory = fetch_json(server.server_port, "/api/cluster/gpus", scope="all")
        replay_inventory = fetch_json(server.server_port, "/api/cluster/gpus", scope="all", trace_id="integration-demo-trace")
        incidents = fetch_json(server.server_port, "/api/cluster/incidents", scope="all")
        sources = fetch_json(server.server_port, "/api/cluster/sources")
        live_history = fetch_json(
            server.server_port,
            "/api/cluster/history",
            scope="live",
            host="live-node-a",
            gpu_index=0,
        )
        replay_history = fetch_json(
            server.server_port,
            "/api/cluster/history",
            scope="all",
            trace_id="integration-demo-trace",
            host="itest-demo-node",
            gpu_index=0,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert all_health["entity_count"] >= 2
    assert all_health["live_entity_count"] >= 1
    assert all_health["replay_entity_count"] >= 1
    assert live_health["scope"] == "live"
    assert live_health["replay_entity_count"] == 0
    assert replay_health["scope"] == "replay"
    assert replay_health["live_entity_count"] == 0
    assert 0.0 <= all_health["avg_confidence"] <= 1.0
    assert set(all_health["scoring_backend_counts"]).issubset({"native", "fallback", "unknown"})
    assert len(inventory["gpus"]) >= 2
    assert {gpu["source"] for gpu in inventory["gpus"]} == {"live", "replay"}
    assert all("scoring_backend" in gpu["regime"] for gpu in inventory["gpus"])
    assert all("confidence" in gpu["regime"] for gpu in inventory["gpus"])
    assert all("provenance" in gpu["regime"] for gpu in inventory["gpus"])
    assert replay_inventory["trace_id"] == "integration-demo-trace"
    assert {gpu["trace_id"] for gpu in replay_inventory["gpus"]} == {"integration-demo-trace"}
    assert incidents["incidents"], incidents
    assert all("scoring_backend" in incident for incident in incidents["incidents"])
    assert all("confidence" in incident for incident in incidents["incidents"])
    assert all("provenance" in incident for incident in incidents["incidents"])
    assert "integration-demo-trace" in sources["trace_ids"]
    assert len(live_history["telemetry"]) >= 12
    assert live_history["regimes"]
    assert replay_history["trace_id"] == "integration-demo-trace"
    assert replay_history["telemetry"]
    assert {row["trace_id"] for row in replay_history["telemetry"]} == {"integration-demo-trace"}


def fetch_json(port: int, path: str, **params):
    query = urlencode(params)
    url = f"http://127.0.0.1:{port}{path}"
    if query:
        url = f"{url}?{query}"
    with urlopen(url) as response:
        return json.loads(response.read().decode("utf-8"))


def _live_memory_pressure_sample(index: int) -> TelemetrySample:
    mem_used_mb = 24_000.0 if index < 6 else 39_200.0
    return TelemetrySample(
        timestamp_ms=1_700_000_000_000 + (index * 5_000),
        host="live-node-a",
        gpu_index=0,
        uuid="GPU-LIVE-001",
        name="NVIDIA A100",
        gpu_util=71.0 if index < 6 else 92.0,
        mem_util=(mem_used_mb / 40_960.0) * 100.0,
        mem_used_mb=mem_used_mb,
        mem_total_mb=40_960.0,
        temperature_c=68.0 if index < 6 else 76.0,
        power_w=228.0 if index < 6 else 268.0,
        power_limit_w=300.0,
        sm_clock_mhz=1410.0,
        mem_clock_mhz=9800.0,
        fan_pct=58.0,
        ecc_errors=0,
        xid_errors=0,
        throttle_reasons=[],
        source="live",
        collection_source="test-collector",
    )
