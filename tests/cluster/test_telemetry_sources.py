from scripts.cluster.telemetry_sources import parse_prometheus_metrics


def test_parse_prometheus_metrics_extracts_dcgm_fields():
    payload = """
# HELP DCGM_FI_DEV_GPU_UTIL GPU utilization
DCGM_FI_DEV_GPU_UTIL{gpu="0",UUID="GPU-123",Hostname="node-a",modelName="NVIDIA A100"} 91
DCGM_FI_DEV_FB_USED{gpu="0",UUID="GPU-123",Hostname="node-a",modelName="NVIDIA A100"} 35840
DCGM_FI_DEV_FB_TOTAL{gpu="0",UUID="GPU-123",Hostname="node-a",modelName="NVIDIA A100"} 40960
DCGM_FI_DEV_GPU_TEMP{gpu="0",UUID="GPU-123",Hostname="node-a",modelName="NVIDIA A100"} 82
DCGM_FI_DEV_POWER_USAGE{gpu="0",UUID="GPU-123",Hostname="node-a",modelName="NVIDIA A100"} 287000
DCGM_FI_DEV_POWER_MGMT_LIMIT{gpu="0",UUID="GPU-123",Hostname="node-a",modelName="NVIDIA A100"} 300000
DCGM_FI_DEV_POWER_VIOLATION{gpu="0",UUID="GPU-123",Hostname="node-a",modelName="NVIDIA A100"} 1
"""
    samples = parse_prometheus_metrics(payload, timestamp_ms=1_700_000_000_000)

    assert len(samples) == 1
    sample = samples[0]
    assert sample.host == "node-a"
    assert sample.gpu_index == 0
    assert sample.uuid == "GPU-123"
    assert sample.name == "NVIDIA A100"
    assert sample.gpu_util == 91.0
    assert round(sample.mem_util, 2) == 87.5
    assert sample.temperature_c == 82.0
    assert sample.power_w == 287.0
    assert sample.power_limit_w == 300.0
    assert "power_cap" in sample.throttle_reasons
