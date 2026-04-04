#!/usr/bin/env python3
"""Incident and cluster-health policy engine for Cluster Sentinel."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cluster.models import IncidentRecord, isoformat_ms
from scripts.cluster.storage import ClusterStore

logger = logging.getLogger("cluster-policy-engine")


@dataclass
class PolicyConfig:
    redis_url: str
    loop_seconds: float
    stale_after_seconds: int
    cluster_name: str


class PolicyEngineService:
    def __init__(self, config: PolicyConfig) -> None:
        self.cfg = config
        self.store = ClusterStore(config.redis_url)
        self._stop = False

    def run(self) -> None:
        logger.info("Cluster policy engine starting for cluster=%s", self.cfg.cluster_name)
        while not self._stop:
            started = time.time()
            try:
                self.run_once()
            except Exception:
                logger.exception("policy iteration failed")
            elapsed = time.time() - started
            time.sleep(max(0.2, self.cfg.loop_seconds - elapsed))

    def run_once(self) -> Dict[str, Any]:
        regimes = active_regimes(self.store, stale_after_seconds=self.cfg.stale_after_seconds, scope="all")
        incidents = evaluate_incidents(regimes, stale_after_seconds=self.cfg.stale_after_seconds)
        collector_status = self.store.read_status("ops:collector_status")
        cluster_health = build_cluster_health(
            cluster_name=self.cfg.cluster_name,
            regimes=regimes,
            incidents=incidents,
            collector_status=collector_status,
        )
        self.store.write_incident_summary(
            {
                "generated_at": isoformat_ms(),
                "stale_after_seconds": self.cfg.stale_after_seconds,
                "incidents": [incident.to_json() for incident in incidents],
            }
        )
        self.store.write_cluster_health(cluster_health)
        self.store.write_status(
            "ops:policy_status",
            {
                "timestamp_ms": int(time.time() * 1000),
                "updated_at": isoformat_ms(),
                "status": "ok",
                "incident_count": len(incidents),
            },
        )
        return cluster_health

    def stop(self) -> None:
        self._stop = True


def evaluate_incidents(regimes: Sequence[Dict[str, Any]], *, stale_after_seconds: int) -> List[IncidentRecord]:
    now_ms = int(time.time() * 1000)
    incidents: List[IncidentRecord] = []
    for regime in regimes:
        if not regime:
            continue
        hazard = float(regime.get("hazard") or 0.0)
        action = str(regime.get("action") or "watch")
        reasons = list(regime.get("reasons") or [])
        age_seconds = max(0.0, (now_ms - int(regime.get("timestamp_ms") or now_ms)) / 1000.0)
        if age_seconds > stale_after_seconds:
            reasons = ["stale_regime_payload", *reasons]
            action = "alert"
        if action == "watch" and hazard < 0.45:
            continue
        severity = severity_for_action(action, hazard)
        host = str(regime.get("host") or "unknown-host")
        gpu_index = int(regime.get("gpu_index") or 0)
        summary = incident_summary(regime=regime, action=action, age_seconds=age_seconds)
        incidents.append(
            IncidentRecord(
                incident_id=f"{host}:{gpu_index}:{action}:{regime.get('regime')}",
                timestamp_ms=int(regime.get("timestamp_ms") or now_ms),
                host=host,
                gpu_index=gpu_index,
                uuid=str(regime.get("uuid") or ""),
                name=str(regime.get("name") or f"GPU {gpu_index}"),
                source=str(regime.get("source") or "live"),
                trace_id=(
                    str(regime["trace_id"])
                    if regime.get("trace_id") not in (None, "")
                    else None
                ),
                severity=severity,
                action=action,
                regime=str(regime.get("regime") or "unknown"),
                hazard=hazard,
                repetitions=int(regime.get("repetitions") or 0),
                scoring_backend=str(regime.get("scoring_backend") or "unknown"),
                confidence=float(regime.get("confidence") or 0.0),
                summary=summary,
                recommended_action=recommended_text(action),
                reasons=reasons,
                provenance=dict(regime.get("provenance") or {}),
            )
        )
    incidents.sort(key=lambda item: (-severity_rank(item.severity), -item.hazard, item.host, item.gpu_index))
    return incidents


def active_regimes(
    store: ClusterStore,
    *,
    stale_after_seconds: int,
    scope: str = "all",
    trace_id: str | None = None,
) -> List[Dict[str, Any]]:
    return [
        entity.get("regime")
        for entity in store.list_entities(scope=scope, stale_after_seconds=stale_after_seconds, trace_id=trace_id)
        if entity.get("regime")
    ]


def build_cluster_health(
    *,
    cluster_name: str,
    regimes: Sequence[Dict[str, Any]],
    incidents: Sequence[IncidentRecord],
    collector_status: Dict[str, Any],
) -> Dict[str, Any]:
    regimes = [regime for regime in regimes if regime]
    hazards = [float(regime.get("hazard") or 0.0) for regime in regimes]
    live_count = sum(1 for regime in regimes if regime.get("source") == "live")
    replay_count = sum(1 for regime in regimes if regime.get("source") == "replay")
    confidences = [float(regime.get("confidence") or 0.0) for regime in regimes]
    action_counts: Dict[str, int] = {}
    regime_counts: Dict[str, int] = {}
    scoring_backend_counts: Dict[str, int] = {}
    hottest = None
    worst = None
    for regime in regimes:
        action = str(regime.get("action") or "watch")
        action_counts[action] = action_counts.get(action, 0) + 1
        label = str(regime.get("regime") or "unknown")
        regime_counts[label] = regime_counts.get(label, 0) + 1
        backend = str(regime.get("scoring_backend") or "unknown")
        scoring_backend_counts[backend] = scoring_backend_counts.get(backend, 0) + 1
        temp = (regime.get("metrics") or {}).get("avg_temperature_c")
        if hottest is None or float(temp or -1.0) > float((hottest.get("metrics") or {}).get("avg_temperature_c") or -1.0):
            hottest = regime
        if worst is None or float(regime.get("hazard") or 0.0) > float(worst.get("hazard") or 0.0):
            worst = regime
    status = "ok"
    if any(incident.severity == "critical" for incident in incidents) or max(hazards or [0.0]) >= 0.85:
        status = "critical"
    elif incidents or max(hazards or [0.0]) >= 0.55:
        status = "warning"
    return {
        "cluster_name": cluster_name,
        "generated_at": isoformat_ms(),
        "status": status,
        "entity_count": len(regimes),
        "live_entity_count": live_count,
        "replay_entity_count": replay_count,
        "node_count": len({str(regime.get("host") or "unknown-host") for regime in regimes}),
        "avg_hazard": round(sum(hazards) / len(hazards), 4) if hazards else 0.0,
        "avg_confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
        "max_hazard": round(max(hazards), 4) if hazards else 0.0,
        "incident_count": len(incidents),
        "critical_incidents": sum(1 for incident in incidents if incident.severity == "critical"),
        "action_counts": action_counts,
        "regime_counts": regime_counts,
        "scoring_backend_counts": scoring_backend_counts,
        "collector_status": collector_status,
        "hottest_gpu": hottest,
        "worst_gpu": worst,
    }


def severity_for_action(action: str, hazard: float) -> str:
    if action == "quarantine":
        return "critical"
    if action in {"drain", "throttle"}:
        return "critical" if hazard >= 0.85 else "warning"
    if action == "alert":
        return "warning"
    return "info"


def severity_rank(severity: str) -> int:
    return {"critical": 3, "warning": 2, "info": 1}.get(severity, 0)


def incident_summary(*, regime: Dict[str, Any], action: str, age_seconds: float) -> str:
    host = str(regime.get("host") or "unknown-host")
    gpu_index = int(regime.get("gpu_index") or 0)
    label = str(regime.get("regime") or "unknown")
    hazard = float(regime.get("hazard") or 0.0)
    base = f"{host} gpu{gpu_index} is {label} with hazard {hazard:.2f}; recommended action: {action}."
    if age_seconds > 0:
        base += f" Signal age {age_seconds:.0f}s."
    reasons = list(regime.get("reasons") or [])
    if reasons:
        base += f" Drivers: {', '.join(reasons[:4])}."
    return base


def recommended_text(action: str) -> str:
    mapping = {
        "watch": "Observe the device and confirm the regime stabilizes.",
        "alert": "Notify the on-call operator and inspect the node/job context.",
        "drain": "Drain or cordon the node before the workload degrades further.",
        "throttle": "Reduce workload intensity or cap clocks/power until thermal pressure falls.",
        "quarantine": "Isolate the device from scheduling and investigate hardware or driver faults.",
    }
    return mapping.get(action, "Inspect the device state.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cluster Sentinel incident policy engine")
    parser.add_argument("--redis", default=os.getenv("VALKEY_URL", "redis://localhost:6379/0"))
    parser.add_argument("--loop-seconds", type=float, default=float(os.getenv("POLICY_LOOP_SECONDS", "5.0")))
    parser.add_argument("--stale-after-seconds", type=int, default=int(os.getenv("POLICY_STALE_AFTER_SECONDS", "30")))
    parser.add_argument("--cluster-name", default=os.getenv("CLUSTER_NAME", "Cluster Sentinel Demo"))
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
    args = build_parser().parse_args()
    cfg = PolicyConfig(
        redis_url=args.redis,
        loop_seconds=max(1.0, float(args.loop_seconds)),
        stale_after_seconds=max(10, int(args.stale_after_seconds)),
        cluster_name=str(args.cluster_name or "Cluster Sentinel Demo"),
    )
    service = PolicyEngineService(cfg)

    def _handle_signal(signum: int, _frame: Any) -> None:
        logger.info("received signal %s, stopping policy engine", signum)
        service.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    if args.once:
        service.run_once()
        return 0
    service.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
