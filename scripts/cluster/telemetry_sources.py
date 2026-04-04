"""Lightweight GPU telemetry collectors for Cluster Sentinel."""
from __future__ import annotations

import csv
import io
import math
import re
import socket
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

from scripts.cluster.models import TelemetrySample

PROM_LINE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>\S+)"
)
LABEL_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)="((?:[^"\\]|\\.)*)"')

DEFAULT_DCGM_URLS = (
    "http://127.0.0.1:9400/metrics",
    "http://localhost:9400/metrics",
    "http://127.0.0.1:9835/metrics",
)

DCGM_METRIC_FIELDS: Dict[str, Tuple[str, str]] = {
    "DCGM_FI_DEV_GPU_UTIL": ("gpu_util", "float"),
    "DCGM_FI_DEV_MEM_COPY_UTIL": ("mem_util", "float"),
    "DCGM_FI_DEV_FB_USED": ("mem_used_mb", "float"),
    "DCGM_FI_DEV_FB_TOTAL": ("mem_total_mb", "float"),
    "DCGM_FI_DEV_GPU_TEMP": ("temperature_c", "float"),
    "DCGM_FI_DEV_POWER_USAGE": ("power_w", "power"),
    "DCGM_FI_DEV_POWER_MGMT_LIMIT": ("power_limit_w", "power"),
    "DCGM_FI_DEV_SM_CLOCK": ("sm_clock_mhz", "float"),
    "DCGM_FI_DEV_MEM_CLOCK": ("mem_clock_mhz", "float"),
    "DCGM_FI_DEV_FAN_SPEED": ("fan_pct", "float"),
    "DCGM_FI_DEV_XID_ERRORS": ("xid_errors", "int"),
    "DCGM_FI_DEV_ECC_DBE_VOL_TOTAL": ("ecc_errors", "int_accumulate"),
    "DCGM_FI_DEV_ECC_SBE_VOL_TOTAL": ("ecc_errors", "int_accumulate"),
    "DCGM_FI_PROF_PCIE_TX_BYTES": ("pcie_tx", "float"),
    "DCGM_FI_PROF_PCIE_RX_BYTES": ("pcie_rx", "float"),
}

DCGM_THROTTLE_METRICS = {
    "DCGM_FI_DEV_POWER_VIOLATION": "power_cap",
    "DCGM_FI_DEV_THERMAL_VIOLATION": "thermal_violation",
    "DCGM_FI_DEV_BOARD_LIMIT_VIOLATION": "board_limit",
    "DCGM_FI_DEV_SYNC_BOOST_VIOLATION": "sync_boost",
    "DCGM_FI_DEV_LOW_UTIL_VIOLATION": "low_utilization",
}

NVIDIA_SMI_FIELD_SETS: Sequence[Sequence[str]] = (
    (
        "index",
        "uuid",
        "name",
        "utilization.gpu",
        "utilization.memory",
        "memory.used",
        "memory.total",
        "temperature.gpu",
        "power.draw",
        "power.limit",
        "clocks.sm",
        "clocks.mem",
        "fan.speed",
        "pci.tx_util",
        "pci.rx_util",
    ),
    (
        "index",
        "uuid",
        "name",
        "utilization.gpu",
        "utilization.memory",
        "memory.used",
        "memory.total",
        "temperature.gpu",
        "power.draw",
        "power.limit",
        "clocks.sm",
        "clocks.mem",
        "fan.speed",
    ),
)


class TelemetrySourceError(RuntimeError):
    """Raised when a telemetry source cannot produce samples."""


def default_hostname() -> str:
    return socket.gethostname()


def parse_prometheus_metrics(
    text: str,
    *,
    timestamp_ms: int,
    host: Optional[str] = None,
    source: str = "live",
    collection_source: str = "dcgm_exporter",
) -> List[TelemetrySample]:
    default_host = host or default_hostname()
    rows: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = PROM_LINE_RE.match(line)
        if not match:
            continue
        metric_name = match.group("name")
        labels = _parse_labels(match.group("labels") or "")
        entity = _entity_from_labels(labels, default_host)
        if entity is None:
            continue
        value = _parse_float(match.group("value"))
        if value is None or math.isnan(value):
            continue
        key = (entity["host"], entity["gpu_index"])
        row = rows.setdefault(
            key,
            {
                "timestamp_ms": timestamp_ms,
                "host": entity["host"],
                "gpu_index": entity["gpu_index"],
                "uuid": entity.get("uuid", ""),
                "name": entity.get("name") or f"GPU {entity['gpu_index']}",
                "gpu_util": 0.0,
                "mem_util": 0.0,
                "mem_used_mb": 0.0,
                "mem_total_mb": 0.0,
                "temperature_c": None,
                "power_w": None,
                "power_limit_w": None,
                "sm_clock_mhz": None,
                "mem_clock_mhz": None,
                "fan_pct": None,
                "ecc_errors": 0,
                "xid_errors": 0,
                "throttle_reasons": [],
                "pcie_tx": None,
                "pcie_rx": None,
                "source": source,
                "collection_source": collection_source,
                "trace_id": None,
            },
        )
        if metric_name in DCGM_METRIC_FIELDS:
            field_name, kind = DCGM_METRIC_FIELDS[metric_name]
            if kind == "float":
                row[field_name] = float(value)
            elif kind == "power":
                row[field_name] = float(value / 1000.0 if value > 10_000 else value)
            elif kind == "int":
                row[field_name] = int(value)
            elif kind == "int_accumulate":
                row[field_name] = int(row.get(field_name) or 0) + int(value)
        elif metric_name in DCGM_THROTTLE_METRICS and value > 0:
            reason = DCGM_THROTTLE_METRICS[metric_name]
            if reason not in row["throttle_reasons"]:
                row["throttle_reasons"].append(reason)
    samples: List[TelemetrySample] = []
    for row in rows.values():
        if not row["mem_util"] and row["mem_total_mb"] > 0:
            row["mem_util"] = min(100.0, (row["mem_used_mb"] / row["mem_total_mb"]) * 100.0)
        samples.append(TelemetrySample.from_mapping(row))
    samples.sort(key=lambda sample: (sample.host, sample.gpu_index))
    return samples


def parse_nvidia_smi_csv(
    text: str,
    *,
    field_names: Sequence[str],
    timestamp_ms: int,
    host: Optional[str] = None,
    source: str = "live",
    collection_source: str = "nvidia_smi",
) -> List[TelemetrySample]:
    rows: List[TelemetrySample] = []
    reader = csv.reader(io.StringIO(text))
    default_host = host or default_hostname()
    for record in reader:
        if not record:
            continue
        cells = [cell.strip() for cell in record]
        if len(cells) < len(field_names):
            continue
        payload = dict(zip(field_names, cells))
        mem_used = _parse_float(payload.get("memory.used")) or 0.0
        mem_total = _parse_float(payload.get("memory.total")) or 0.0
        mem_util = _parse_float(payload.get("utilization.memory"))
        if mem_util is None and mem_total > 0:
            mem_util = (mem_used / mem_total) * 100.0
        rows.append(
            TelemetrySample(
                timestamp_ms=timestamp_ms,
                host=default_host,
                gpu_index=int(_parse_float(payload.get("index")) or 0),
                uuid=str(payload.get("uuid") or ""),
                name=str(payload.get("name") or "GPU"),
                gpu_util=_parse_float(payload.get("utilization.gpu")) or 0.0,
                mem_util=mem_util or 0.0,
                mem_used_mb=mem_used,
                mem_total_mb=mem_total,
                temperature_c=_parse_float(payload.get("temperature.gpu")),
                power_w=_parse_float(payload.get("power.draw")),
                power_limit_w=_parse_float(payload.get("power.limit")),
                sm_clock_mhz=_parse_float(payload.get("clocks.sm")),
                mem_clock_mhz=_parse_float(payload.get("clocks.mem")),
                fan_pct=_parse_float(payload.get("fan.speed")),
                ecc_errors=0,
                xid_errors=0,
                throttle_reasons=[],
                pcie_tx=_parse_float(payload.get("pci.tx_util")),
                pcie_rx=_parse_float(payload.get("pci.rx_util")),
                source=source,
                collection_source=collection_source,
            )
        )
    return rows


@dataclass
class PrometheusDcgmSource:
    urls: Sequence[str]
    host: str
    timeout_seconds: float = 0.75
    name: str = "dcgm_exporter"

    def collect(self, *, timestamp_ms: int) -> List[TelemetrySample]:
        errors: List[str] = []
        for url in self.urls:
            try:
                response = requests.get(url, timeout=self.timeout_seconds)
                response.raise_for_status()
                samples = parse_prometheus_metrics(
                    response.text,
                    timestamp_ms=timestamp_ms,
                    host=self.host,
                    collection_source=self.name,
                )
                if samples:
                    return samples
                errors.append(f"{url}: no samples")
            except requests.RequestException as exc:
                errors.append(f"{url}: {exc}")
        raise TelemetrySourceError("; ".join(errors) or "dcgm exporter unavailable")


class NvmlSource:
    name = "nvml"

    def __init__(self, *, host: str) -> None:
        try:
            import pynvml  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise TelemetrySourceError("pynvml is not installed") from exc
        self._pynvml = pynvml
        try:
            pynvml.nvmlInit()
        except Exception as exc:  # pragma: no cover
            raise TelemetrySourceError(str(exc)) from exc
        self.host = host

    def collect(self, *, timestamp_ms: int) -> List[TelemetrySample]:
        pynvml = self._pynvml
        try:
            count = int(pynvml.nvmlDeviceGetCount())
        except Exception as exc:
            raise TelemetrySourceError(str(exc)) from exc
        rows: List[TelemetrySample] = []
        for index in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            util = _nvml_call(pynvml.nvmlDeviceGetUtilizationRates, handle)
            memory = _nvml_call(pynvml.nvmlDeviceGetMemoryInfo, handle)
            temp = _nvml_call(
                pynvml.nvmlDeviceGetTemperature,
                handle,
                getattr(pynvml, "NVML_TEMPERATURE_GPU", 0),
            )
            power_usage = _nvml_call(pynvml.nvmlDeviceGetPowerUsage, handle)
            power_limit = _nvml_call(pynvml.nvmlDeviceGetEnforcedPowerLimit, handle)
            sm_clock = _nvml_call(
                pynvml.nvmlDeviceGetClockInfo,
                handle,
                getattr(pynvml, "NVML_CLOCK_SM", 1),
            )
            mem_clock = _nvml_call(
                pynvml.nvmlDeviceGetClockInfo,
                handle,
                getattr(pynvml, "NVML_CLOCK_MEM", 2),
            )
            fan_pct = _nvml_call(pynvml.nvmlDeviceGetFanSpeed, handle)
            throttle_mask = _nvml_call(pynvml.nvmlDeviceGetCurrentClocksThrottleReasons, handle) or 0
            tx_value = _nvml_call(
                pynvml.nvmlDeviceGetPcieThroughput,
                handle,
                getattr(pynvml, "NVML_PCIE_UTIL_TX_BYTES", 0),
            )
            rx_value = _nvml_call(
                pynvml.nvmlDeviceGetPcieThroughput,
                handle,
                getattr(pynvml, "NVML_PCIE_UTIL_RX_BYTES", 1),
            )
            ecc_total = 0
            corrected = _nvml_call(
                getattr(pynvml, "nvmlDeviceGetTotalEccErrors", None),
                handle,
                getattr(pynvml, "NVML_MEMORY_ERROR_TYPE_CORRECTED", 0),
                getattr(pynvml, "NVML_VOLATILE_ECC", 0),
            )
            uncorrected = _nvml_call(
                getattr(pynvml, "nvmlDeviceGetTotalEccErrors", None),
                handle,
                getattr(pynvml, "NVML_MEMORY_ERROR_TYPE_UNCORRECTED", 1),
                getattr(pynvml, "NVML_VOLATILE_ECC", 0),
            )
            if corrected is not None:
                ecc_total += int(corrected)
            if uncorrected is not None:
                ecc_total += int(uncorrected)
            rows.append(
                TelemetrySample(
                    timestamp_ms=timestamp_ms,
                    host=self.host,
                    gpu_index=index,
                    uuid=_decode_bytes(_nvml_call(pynvml.nvmlDeviceGetUUID, handle)) or "",
                    name=_decode_bytes(_nvml_call(pynvml.nvmlDeviceGetName, handle)) or f"GPU {index}",
                    gpu_util=float(getattr(util, "gpu", 0.0) if util is not None else 0.0),
                    mem_util=float(getattr(util, "memory", 0.0) if util is not None else 0.0),
                    mem_used_mb=float(getattr(memory, "used", 0.0) or 0.0) / (1024.0 * 1024.0),
                    mem_total_mb=float(getattr(memory, "total", 0.0) or 0.0) / (1024.0 * 1024.0),
                    temperature_c=float(temp) if temp is not None else None,
                    power_w=(float(power_usage) / 1000.0) if power_usage is not None else None,
                    power_limit_w=(float(power_limit) / 1000.0) if power_limit is not None else None,
                    sm_clock_mhz=float(sm_clock) if sm_clock is not None else None,
                    mem_clock_mhz=float(mem_clock) if mem_clock is not None else None,
                    fan_pct=float(fan_pct) if fan_pct is not None else None,
                    ecc_errors=ecc_total,
                    xid_errors=0,
                    throttle_reasons=_decode_throttle_mask(self._pynvml, int(throttle_mask or 0)),
                    pcie_tx=float(tx_value) if tx_value is not None else None,
                    pcie_rx=float(rx_value) if rx_value is not None else None,
                    source="live",
                    collection_source=self.name,
                )
            )
        if not rows:
            raise TelemetrySourceError("nvml reported no devices")
        return rows


@dataclass
class NvidiaSmiSource:
    host: str
    timeout_seconds: float = 2.0
    name: str = "nvidia_smi"

    def collect(self, *, timestamp_ms: int) -> List[TelemetrySample]:
        errors: List[str] = []
        for field_names in NVIDIA_SMI_FIELD_SETS:
            command = [
                "nvidia-smi",
                f"--query-gpu={','.join(field_names)}",
                "--format=csv,noheader,nounits",
            ]
            try:
                result = subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
                samples = parse_nvidia_smi_csv(
                    result.stdout,
                    field_names=field_names,
                    timestamp_ms=timestamp_ms,
                    host=self.host,
                    collection_source=self.name,
                )
                if samples:
                    return samples
                errors.append("nvidia-smi returned no rows")
            except FileNotFoundError as exc:
                raise TelemetrySourceError("nvidia-smi not found") from exc
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                errors.append(str(exc))
        raise TelemetrySourceError("; ".join(errors) or "nvidia-smi unavailable")


def _parse_labels(blob: str) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    for key, value in LABEL_RE.findall(blob):
        labels[key] = bytes(value, "utf-8").decode("unicode_escape")
    return labels


def _entity_from_labels(labels: Dict[str, str], default_host: str) -> Optional[Dict[str, Any]]:
    gpu_raw = labels.get("gpu") or labels.get("minor_number")
    if gpu_raw is None:
        device = labels.get("device", "")
        digits = re.findall(r"\d+", device)
        gpu_raw = digits[-1] if digits else None
    if gpu_raw is None:
        return None
    try:
        gpu_index = int(gpu_raw)
    except (TypeError, ValueError):
        return None
    instance = labels.get("Hostname") or labels.get("instance") or default_host
    host = instance.split(":", 1)[0] if instance else default_host
    return {
        "host": host,
        "gpu_index": gpu_index,
        "uuid": labels.get("UUID") or labels.get("uuid") or "",
        "name": labels.get("modelName") or labels.get("name") or labels.get("gpu_product_name") or f"GPU {gpu_index}",
    }


def _parse_float(value: Any) -> Optional[float]:
    if value in (None, "", "N/A", "[Not Supported]", "Not Supported"):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _decode_bytes(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", "ignore")
    return str(value)


def _nvml_call(fn: Any, *args: Any) -> Any:
    if fn is None:
        return None
    try:
        return fn(*args)
    except Exception:
        return None


def _decode_throttle_mask(pynvml: Any, mask: int) -> List[str]:
    if not mask:
        return []
    mapping = {
        "gpu_idle": getattr(pynvml, "nvmlClocksThrottleReasonGpuIdle", 0),
        "applications_clocks": getattr(pynvml, "nvmlClocksThrottleReasonApplicationsClocksSetting", 0),
        "sw_power_cap": getattr(pynvml, "nvmlClocksThrottleReasonSwPowerCap", 0),
        "hw_slowdown": getattr(pynvml, "nvmlClocksThrottleReasonHwSlowdown", 0),
        "sync_boost": getattr(pynvml, "nvmlClocksThrottleReasonSyncBoost", 0),
        "sw_thermal_slowdown": getattr(pynvml, "nvmlClocksThrottleReasonSwThermalSlowdown", 0),
        "hw_thermal_slowdown": getattr(pynvml, "nvmlClocksThrottleReasonHwThermalSlowdown", 0),
        "hw_power_brake": getattr(pynvml, "nvmlClocksThrottleReasonHwPowerBrakeSlowdown", 0),
        "display_clock": getattr(pynvml, "nvmlClocksThrottleReasonDisplayClockSetting", 0),
    }
    return [name for name, bit in mapping.items() if bit and mask & int(bit)]


def build_default_source_order(
    *,
    host: Optional[str] = None,
    dcgm_urls: Optional[Iterable[str]] = None,
) -> List[Any]:
    resolved_host = host or default_hostname()
    return [
        PrometheusDcgmSource(tuple(dcgm_urls or DEFAULT_DCGM_URLS), resolved_host),
        NvmlSource(host=resolved_host),
        NvidiaSmiSource(host=resolved_host),
    ]
