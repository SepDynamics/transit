from scripts.cluster.models import TelemetrySample
from scripts.cluster.storage import ClusterStore


def test_stale_live_entities_drop_from_active_inventory(valkey_url):
    store = ClusterStore(valkey_url)
    stale_sample = TelemetrySample(
        timestamp_ms=1_700_000_000_000,
        host="live-node-a",
        gpu_index=0,
        uuid="GPU-LIVE-001",
        name="GPU 0",
        gpu_util=78.0,
        mem_util=72.0,
        mem_used_mb=29_000.0,
        mem_total_mb=40_960.0,
        temperature_c=69.0,
        power_w=240.0,
        power_limit_w=300.0,
        source="live",
        collection_source="test",
    )
    fresh_sample = TelemetrySample(
        timestamp_ms=1_700_000_060_000,
        host="live-node-b",
        gpu_index=0,
        uuid="GPU-LIVE-002",
        name="GPU 0",
        gpu_util=81.0,
        mem_util=74.0,
        mem_used_mb=30_000.0,
        mem_total_mb=40_960.0,
        temperature_c=70.0,
        power_w=244.0,
        power_limit_w=300.0,
        source="live",
        collection_source="test",
    )
    store.record_sample(stale_sample)
    store.record_sample(fresh_sample)

    active = store.list_entities(
        scope="live",
        stale_after_seconds=30,
        now_ms=stale_sample.timestamp_ms + 60_000,
    )

    assert [entity["host"] for entity in active] == ["live-node-b"]


def test_expired_replay_entities_can_be_purged_without_flushing_all_state(valkey_url):
    store = ClusterStore(valkey_url)
    replay_sample = TelemetrySample(
        timestamp_ms=1_700_000_000_000,
        host="replay-node-a",
        gpu_index=0,
        uuid="GPU-REPLAY-001",
        name="GPU 0",
        gpu_util=92.0,
        mem_util=95.0,
        mem_used_mb=39_000.0,
        mem_total_mb=40_960.0,
        temperature_c=83.0,
        power_w=292.0,
        power_limit_w=300.0,
        source="replay",
        collection_source="replay",
        trace_id="trace-alpha",
    )
    live_sample = TelemetrySample(
        timestamp_ms=1_700_000_060_000,
        host="live-node-a",
        gpu_index=0,
        uuid="GPU-LIVE-001",
        name="GPU 0",
        gpu_util=72.0,
        mem_util=62.0,
        mem_used_mb=25_000.0,
        mem_total_mb=40_960.0,
        temperature_c=67.0,
        power_w=221.0,
        power_limit_w=300.0,
        source="live",
        collection_source="test",
    )
    store.record_sample(replay_sample)
    store.record_sample(live_sample)

    removed = store.purge_expired_entities(
        scope="replay",
        stale_after_seconds=30,
        now_ms=replay_sample.timestamp_ms + 60_000,
    )

    assert removed == [
        {
            "host": "replay-node-a",
            "gpu_index": 0,
            "source": "replay",
            "trace_id": "trace-alpha",
            "last_seen_ms": replay_sample.timestamp_ms,
        }
    ]
    assert store.list_trace_ids() == []
    assert [entity["host"] for entity in store.list_entities(scope="all")] == ["live-node-a"]
