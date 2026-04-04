from scripts.cluster.replay import flatten_trace, replay_trace, resolve_scenario
from scripts.cluster.storage import ClusterStore


def test_replay_trace_preserves_collection_source_for_file_traces(valkey_url):
    store = ClusterStore(valkey_url)
    trace = {
        "trace_id": "proof-trace",
        "entities": [
            {
                "host": "source-node",
                "gpu_index": 0,
                "samples": [
                    {
                        "timestamp_ms": 1_700_000_000_000,
                        "host": "source-node",
                        "gpu_index": 0,
                        "uuid": "GPU-001",
                        "name": "GPU 0",
                        "gpu_util": 55.0,
                        "mem_util": 61.0,
                        "mem_used_mb": 25_000.0,
                        "mem_total_mb": 40_960.0,
                        "temperature_c": 71.0,
                        "power_w": 240.0,
                        "power_limit_w": 300.0,
                        "ecc_errors": 0,
                        "xid_errors": 0,
                        "throttle_reasons": [],
                        "source": "live",
                        "collection_source": "gwdg_zenodo",
                        "trace_id": "proof-trace",
                    }
                ],
            }
        ],
    }

    emitted = replay_trace(
        store,
        trace,
        trace_id="proof-trace",
        scenario="none",
        speed=1_000_000.0,
        host_prefix="proof",
        retention=10,
    )

    assert emitted == 1
    sample = store.get_latest_sample("proof-source-node", 0)
    assert sample["source"] == "replay"
    assert sample["collection_source"] == "gwdg_zenodo"
    assert sample["trace_id"] == "proof-trace"


def test_resolve_scenario_defaults_to_none_for_file_trace_and_mixed_for_demo():
    assert resolve_scenario("auto", trace_path="trace.json", demo=False) == "none"
    assert resolve_scenario("auto", trace_path=None, demo=False) == "mixed"
    assert resolve_scenario("auto", trace_path="trace.json", demo=True) == "mixed"


def test_flatten_trace_preserves_global_relative_offsets_across_entities():
    trace = {
        "trace_id": "staggered-trace",
        "entities": [
            {
                "host": "node-a",
                "gpu_index": 0,
                "samples": [
                    {"timestamp_ms": 1_700_000_000_000, "host": "node-a", "gpu_index": 0},
                    {"timestamp_ms": 1_700_000_005_000, "host": "node-a", "gpu_index": 0},
                ],
            },
            {
                "host": "node-b",
                "gpu_index": 1,
                "samples": [
                    {"timestamp_ms": 1_700_000_002_000, "host": "node-b", "gpu_index": 1},
                    {"timestamp_ms": 1_700_000_007_000, "host": "node-b", "gpu_index": 1},
                ],
            },
        ],
    }

    events = flatten_trace(trace)

    assert [(offset, entity["host"], entity["gpu_index"]) for offset, entity, _ in events] == [
        (0, "node-a", 0),
        (2000, "node-b", 1),
        (5000, "node-a", 0),
        (7000, "node-b", 1),
    ]
