#!/usr/bin/env python3
"""Live telemetry collector for Cluster Sentinel."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cluster.models import isoformat_ms
from scripts.cluster.storage import ClusterStore
from scripts.cluster.telemetry_sources import (
    DEFAULT_DCGM_URLS,
    NvidiaSmiSource,
    NvmlSource,
    PrometheusDcgmSource,
    TelemetrySourceError,
    default_hostname,
)

logger = logging.getLogger("cluster-telemetry-collector")


@dataclass
class CollectorConfig:
    redis_url: str
    interval_seconds: float
    sample_retention: int
    preferred_source: str
    host: str
    dcgm_urls: List[str]


class TelemetryCollectorService:
    def __init__(self, config: CollectorConfig) -> None:
        self.cfg = config
        self.store = ClusterStore(config.redis_url)
        self._stop = False
        self._source: Optional[Any] = None

    def run(self) -> None:
        logger.info("Cluster telemetry collector starting on host=%s", self.cfg.host)
        while not self._stop:
            started = time.time()
            try:
                self.run_once()
            except Exception:
                logger.exception("collector iteration failed")
            elapsed = time.time() - started
            time.sleep(max(0.2, self.cfg.interval_seconds - elapsed))

    def run_once(self) -> List[dict]:
        timestamp_ms = int(time.time() * 1000)
        source = self._source or self._select_source()
        try:
            samples = source.collect(timestamp_ms=timestamp_ms)
        except TelemetrySourceError as exc:
            logger.warning("source %s failed: %s", getattr(source, "name", "unknown"), exc)
            self._source = None
            self.store.write_status(
                "ops:collector_status",
                {
                    "timestamp_ms": timestamp_ms,
                    "updated_at": isoformat_ms(timestamp_ms),
                    "status": "error",
                    "host": self.cfg.host,
                    "preferred_source": self.cfg.preferred_source,
                    "error": str(exc),
                },
            )
            return []
        self._source = source
        for sample in samples:
            self.store.record_sample(sample, retention=self.cfg.sample_retention)
        self.store.write_status(
            "ops:collector_status",
            {
                "timestamp_ms": timestamp_ms,
                "updated_at": isoformat_ms(timestamp_ms),
                "status": "ok" if samples else "idle",
                "host": self.cfg.host,
                "preferred_source": self.cfg.preferred_source,
                "selected_source": getattr(source, "name", "unknown"),
                "sample_count": len(samples),
            },
        )
        if samples:
            logger.info(
                "ingested %d telemetry samples via %s",
                len(samples),
                getattr(source, "name", "unknown"),
            )
        return [sample.to_json() for sample in samples]

    def stop(self) -> None:
        self._stop = True

    def _select_source(self) -> Any:
        preferred = self.cfg.preferred_source.strip().lower()
        candidate_names: Iterable[str]
        if preferred == "dcgm_exporter":
            candidate_names = ["dcgm_exporter"]
        elif preferred == "nvml":
            candidate_names = ["nvml"]
        elif preferred == "nvidia_smi":
            candidate_names = ["nvidia_smi"]
        else:
            candidate_names = ["dcgm_exporter", "nvml", "nvidia_smi"]
        errors: List[str] = []
        timestamp_ms = int(time.time() * 1000)
        for name in candidate_names:
            try:
                candidate = self._instantiate_source(name)
                samples = candidate.collect(timestamp_ms=timestamp_ms)
                if samples:
                    logger.info("selected telemetry source: %s", candidate.name)
                    return candidate
                errors.append(f"{candidate.name}: no samples")
            except TelemetrySourceError as exc:
                errors.append(f"{name}: {exc}")
        raise TelemetrySourceError("; ".join(errors) or "no telemetry source available")

    def _instantiate_source(self, name: str) -> Any:
        if name == "dcgm_exporter":
            return PrometheusDcgmSource(self.cfg.dcgm_urls, self.cfg.host)
        if name == "nvml":
            return NvmlSource(host=self.cfg.host)
        if name == "nvidia_smi":
            return NvidiaSmiSource(host=self.cfg.host)
        raise TelemetrySourceError(f"unsupported source {name}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect live GPU telemetry into Valkey")
    parser.add_argument("--redis", default=os.getenv("VALKEY_URL", "redis://localhost:6379/0"))
    parser.add_argument("--interval", type=float, default=float(os.getenv("CLUSTER_COLLECTION_INTERVAL", "5.0")))
    parser.add_argument("--sample-retention", type=int, default=int(os.getenv("CLUSTER_SAMPLE_RETENTION", "720")))
    parser.add_argument("--source", default=os.getenv("CLUSTER_TELEMETRY_SOURCE", "auto"))
    parser.add_argument("--host", default=os.getenv("CLUSTER_HOSTNAME", default_hostname()))
    parser.add_argument(
        "--dcgm-url",
        dest="dcgm_urls",
        action="append",
        default=[],
        help="DCGM exporter metrics URL. Can be provided multiple times.",
    )
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
    args = build_parser().parse_args()
    cfg = CollectorConfig(
        redis_url=args.redis,
        interval_seconds=max(1.0, float(args.interval)),
        sample_retention=max(60, int(args.sample_retention)),
        preferred_source=str(args.source or "auto"),
        host=str(args.host or default_hostname()),
        dcgm_urls=list(args.dcgm_urls or DEFAULT_DCGM_URLS),
    )
    service = TelemetryCollectorService(cfg)

    def _handle_signal(signum: int, _frame: Any) -> None:
        logger.info("received signal %s, stopping collector", signum)
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
