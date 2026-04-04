"""Shared data models for Cluster Sentinel."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from scripts.shared.runtime import clamp, isoformat_ms


@dataclass
class TelemetrySample:
    timestamp_ms: int
    host: str
    gpu_index: int
    uuid: str
    name: str
    gpu_util: float
    mem_util: float
    mem_used_mb: float
    mem_total_mb: float
    temperature_c: Optional[float] = None
    power_w: Optional[float] = None
    power_limit_w: Optional[float] = None
    sm_clock_mhz: Optional[float] = None
    mem_clock_mhz: Optional[float] = None
    fan_pct: Optional[float] = None
    ecc_errors: int = 0
    xid_errors: int = 0
    throttle_reasons: List[str] = field(default_factory=list)
    pcie_tx: Optional[float] = None
    pcie_rx: Optional[float] = None
    source: str = "live"
    collection_source: str = "unknown"
    trace_id: Optional[str] = None

    def entity_id(self) -> str:
        return f"{self.host}:{self.gpu_index}"

    def entity_token(self) -> str:
        return f"{self.host}|{self.gpu_index}"

    def to_json(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["gpu_util"] = float(self.gpu_util)
        payload["mem_util"] = float(self.mem_util)
        payload["mem_used_mb"] = float(self.mem_used_mb)
        payload["mem_total_mb"] = float(self.mem_total_mb)
        return payload

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "TelemetrySample":
        return cls(
            timestamp_ms=int(payload.get("timestamp_ms") or 0),
            host=str(payload.get("host") or "unknown-host"),
            gpu_index=int(payload.get("gpu_index") or 0),
            uuid=str(payload.get("uuid") or ""),
            name=str(payload.get("name") or "GPU"),
            gpu_util=float(payload.get("gpu_util") or 0.0),
            mem_util=float(payload.get("mem_util") or 0.0),
            mem_used_mb=float(payload.get("mem_used_mb") or 0.0),
            mem_total_mb=float(payload.get("mem_total_mb") or 0.0),
            temperature_c=_optional_float(payload.get("temperature_c")),
            power_w=_optional_float(payload.get("power_w")),
            power_limit_w=_optional_float(payload.get("power_limit_w")),
            sm_clock_mhz=_optional_float(payload.get("sm_clock_mhz")),
            mem_clock_mhz=_optional_float(payload.get("mem_clock_mhz")),
            fan_pct=_optional_float(payload.get("fan_pct")),
            ecc_errors=int(payload.get("ecc_errors") or 0),
            xid_errors=int(payload.get("xid_errors") or 0),
            throttle_reasons=list(payload.get("throttle_reasons") or []),
            pcie_tx=_optional_float(payload.get("pcie_tx")),
            pcie_rx=_optional_float(payload.get("pcie_rx")),
            source=str(payload.get("source") or "live"),
            collection_source=str(payload.get("collection_source") or "unknown"),
            trace_id=(
                str(payload["trace_id"])
                if payload.get("trace_id") not in (None, "")
                else None
            ),
        )


@dataclass
class RegimeRecord:
    timestamp_ms: int
    host: str
    gpu_index: int
    uuid: str
    name: str
    source: str
    collection_source: str
    trace_id: Optional[str]
    regime: str
    hazard: float
    repetitions: int
    action: str
    scoring_backend: str
    confidence: float
    signature: str
    coherence: float
    stability: float
    entropy: float
    rupture: float
    reasons: List[str]
    provenance: Dict[str, Any]
    metrics: Dict[str, Any]

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IncidentRecord:
    incident_id: str
    timestamp_ms: int
    host: str
    gpu_index: int
    uuid: str
    name: str
    source: str
    trace_id: Optional[str]
    severity: str
    action: str
    regime: str
    hazard: float
    repetitions: int
    scoring_backend: str
    confidence: float
    summary: str
    recommended_action: str
    reasons: List[str]
    provenance: Dict[str, Any]
    status: str = "open"

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, "", "N/A", "[Not Supported]"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
