#!/usr/bin/env python3
"""Persist current transit feeds into the rolling Valkey store."""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.shared.runtime import isoformat_ms
from scripts.transit.agencies import default_transit_agency_key, get_transit_agency_adapter
from scripts.transit.domain import TransitRuntimeConfig, TransitSnapshotService
from scripts.transit.evidence import EvidenceArchive
from scripts.transit.store import TransitStore

logger = logging.getLogger("transit-ingest")


@dataclass
class TransitIngestConfig:
    redis_url: str
    interval_seconds: float
    history_retention: int
    runtime: TransitRuntimeConfig
    history_interval_seconds: float = 30.0
    materialize_read_models: bool = True
    read_model_scorecard_limit: int = 60
    read_model_trends_limit: int = 6
    read_model_trends_window: int = 24
    profile_enabled: bool = False
    evidence_root: Optional[Path] = None


class TransitIngestService:
    def __init__(self, config: TransitIngestConfig, *, store: Optional[TransitStore] = None) -> None:
        self.cfg = config
        self.store = store or TransitStore(config.redis_url)
        self.snapshot_service = TransitSnapshotService(config.runtime)
        self.evidence_archive = (
            EvidenceArchive(config.evidence_root) if config.evidence_root else None
        )
        self._stop = False
        self._last_history_write_at = 0.0
        self._read_model_rollups_ready = False

    def run(self) -> None:
        logger.info("Transit ingest service starting for system=%s", self.cfg.runtime.system_name)
        while not self._stop:
            started = time.time()
            try:
                self.run_once()
            except Exception:
                logger.exception("transit ingest iteration failed")
            elapsed = time.time() - started
            time.sleep(max(0.2, self.cfg.interval_seconds - elapsed))

    def run_once(self) -> Dict[str, Any]:
        stage_timings: list[Dict[str, Any]] = []
        started_wall = time.perf_counter()
        started_cpu = time.process_time()
        last_wall = started_wall
        last_cpu = started_cpu

        def mark_stage(stage: str) -> None:
            nonlocal last_wall, last_cpu
            if not self.cfg.profile_enabled:
                return
            now_wall = time.perf_counter()
            now_cpu = time.process_time()
            stage_timings.append(
                {
                    "stage": stage,
                    "wall_ms": round((now_wall - last_wall) * 1000.0, 2),
                    "cpu_ms": round((now_cpu - last_cpu) * 1000.0, 2),
                }
            )
            last_wall = now_wall
            last_cpu = now_cpu

        payload = self.snapshot_service.snapshot()
        mark_stage("snapshot")
        configured_feeds = {
            "static_gtfs": bool(self.cfg.runtime.static_feed),
            "vehicle_positions": bool(self.cfg.runtime.vehicle_positions_feed),
            "trip_updates": bool(self.cfg.runtime.trip_updates_feed),
            "alerts": bool(self.cfg.runtime.alerts_feed),
            "event_overlays": bool(self.cfg.runtime.event_overlays_feed),
        }
        write_history = self._should_write_history()
        snapshot_parts = self.store.write_snapshot(
            payload,
            configured_feeds=configured_feeds,
            retention=self.cfg.history_retention,
            write_history=write_history,
        )
        mark_stage("write_snapshot")
        if write_history:
            self._last_history_write_at = time.monotonic()
        read_model_status: Dict[str, Any] = {"enabled": self.cfg.materialize_read_models}
        if self.cfg.materialize_read_models:
            try:
                if write_history:
                    should_refresh_rollups = True
                elif self._read_model_rollups_ready:
                    should_refresh_rollups = False
                else:
                    should_refresh_rollups = not (
                        self.store.read_live_read_model("trends")
                        and self.store.read_live_read_model("scorecard")
                    )
                read_models = self.store.write_live_read_models(
                    scorecard_limit=self.cfg.read_model_scorecard_limit,
                    trends_limit=self.cfg.read_model_trends_limit,
                    trends_window=self.cfg.read_model_trends_window,
                    include_scorecard=should_refresh_rollups,
                    include_trends=should_refresh_rollups,
                    include_dashboard=True,
                    snapshot_parts=snapshot_parts,
                )
                read_model_status["updated"] = sorted(read_models.keys())
            except Exception:
                logger.exception("failed to materialize live read models")
                read_model_status["status"] = "error"
                self._read_model_rollups_ready = False
            else:
                read_model_status["status"] = "ok"
                if should_refresh_rollups:
                    self._read_model_rollups_ready = {
                        "scorecard",
                        "trends",
                    } <= set(read_models)
                elif self._read_model_rollups_ready:
                    self._read_model_rollups_ready = True
        mark_stage("read_models")
        manifest = _load_current_manifest(self.cfg.runtime.static_feed)
        evidence_status: Dict[str, Any] = {"enabled": bool(self.evidence_archive)}
        if self.evidence_archive and write_history:
            try:
                evidence_path = self.evidence_archive.append_snapshot(
                    payload,
                    agency_key=self.cfg.runtime.agency_key,
                    archive_manifest=manifest,
                )
                evidence_status.update({"status": "ok", "path": str(evidence_path)})
            except Exception as exc:
                logger.exception("failed to write durable evidence snapshot")
                evidence_status.update({"status": "error", "error": str(exc)})
        status = {
            "system_name": self.cfg.runtime.system_name,
            "agency_key": self.cfg.runtime.agency_key,
            "status": "ok" if not (payload.get("errors") or []) else "degraded",
            "updated_at": isoformat_ms(),
            "feed_status": payload.get("feed_status") or {},
            "errors": list(payload.get("errors") or []),
            "read_models": read_model_status,
            "durable_evidence": evidence_status,
        }
        if manifest:
            status["archive_manifest"] = manifest
        if self.cfg.profile_enabled:
            status["profile"] = {
                "stages": stage_timings,
                "total_wall_ms_before_status_write": round(
                    (time.perf_counter() - started_wall) * 1000.0, 2
                ),
                "total_cpu_ms_before_status_write": round(
                    (time.process_time() - started_cpu) * 1000.0, 2
                ),
            }
        self.store.write_status("ops:transit_ingest_status", status)
        mark_stage("status_write")
        logger.info(
            "persisted transit snapshot with %d vehicles and %d incidents history=%s",
            len((payload.get("entities") or {}).get("vehicles") or []),
            len((payload.get("incidents") or {}).get("incidents") or []),
            write_history,
        )
        if self.cfg.profile_enabled:
            logger.info(
                "transit ingest profile wall_ms=%.2f cpu_ms=%.2f stages=%s",
                (time.perf_counter() - started_wall) * 1000.0,
                (time.process_time() - started_cpu) * 1000.0,
                stage_timings,
            )
        return payload

    def stop(self) -> None:
        self._stop = True

    def _should_write_history(self) -> bool:
        interval = float(self.cfg.history_interval_seconds or 0.0)
        if interval <= 0:
            return True
        if self._last_history_write_at <= 0:
            return True
        return (time.monotonic() - self._last_history_write_at) >= interval


def build_parser() -> argparse.ArgumentParser:
    adapter = get_transit_agency_adapter(os.getenv("TRANSIT_AGENCY", default_transit_agency_key()))
    default_feed_paths = adapter.default_feed_paths()
    parser = argparse.ArgumentParser(description="Persist current transit feeds into Valkey")
    parser.add_argument("--redis", default=os.getenv("VALKEY_URL", "redis://localhost:6379/0"))
    parser.add_argument("--interval", type=float, default=float(os.getenv("TRANSIT_INGEST_INTERVAL_SECONDS", "5")))
    parser.add_argument("--history-retention", type=int, default=int(os.getenv("TRANSIT_HISTORY_RETENTION", "720")))
    parser.add_argument("--history-interval", type=float, default=float(os.getenv("TRANSIT_HISTORY_INTERVAL_SECONDS", "30")))
    parser.add_argument(
        "--read-models",
        action=argparse.BooleanOptionalAction,
        default=_bool_env("TRANSIT_READ_MODELS_ENABLED", True),
        help="materialize live scorecard/trends/dashboard read models",
    )
    parser.add_argument(
        "--read-model-scorecard-limit",
        type=int,
        default=int(os.getenv("TRANSIT_READ_MODEL_SCORECARD_LIMIT", "60")),
    )
    parser.add_argument(
        "--read-model-trends-limit",
        type=int,
        default=int(os.getenv("TRANSIT_READ_MODEL_TRENDS_LIMIT", "6")),
    )
    parser.add_argument(
        "--read-model-trends-window",
        type=int,
        default=int(os.getenv("TRANSIT_READ_MODEL_TRENDS_WINDOW", "24")),
    )
    parser.add_argument(
        "--profile",
        action=argparse.BooleanOptionalAction,
        default=_bool_env("TRANSIT_INGEST_PROFILE", False),
        help="record per-stage wall and CPU timings in ingest status and logs",
    )
    parser.add_argument(
        "--evidence-root",
        default=os.getenv("TRANSIT_EVIDENCE_ROOT", "data/evidence"),
        help="durable JSONL evidence root; set empty to disable",
    )
    parser.add_argument("--agency", default=os.getenv("TRANSIT_AGENCY", adapter.key))
    parser.add_argument("--system-name", default=os.getenv("TRANSIT_SYSTEM_NAME", adapter.system_name))
    parser.add_argument("--static-feed", default=os.getenv("TRANSIT_GTFS_STATIC_PATH", default_feed_paths["static_gtfs"]))
    parser.add_argument(
        "--vehicle-positions-feed",
        default=os.getenv("TRANSIT_GTFS_RT_VEHICLE_POSITIONS_PATH", default_feed_paths["vehicle_positions"]),
    )
    parser.add_argument(
        "--trip-updates-feed",
        default=os.getenv("TRANSIT_GTFS_RT_TRIP_UPDATES_PATH", default_feed_paths["trip_updates"]),
    )
    parser.add_argument(
        "--alerts-feed",
        default=os.getenv("TRANSIT_GTFS_RT_ALERTS_PATH", default_feed_paths["alerts"]),
    )
    parser.add_argument("--event-overlays-feed", default=os.getenv("TRANSIT_EVENT_OVERLAYS_PATH", ""))
    parser.add_argument("--stale-after-seconds", type=int, default=int(os.getenv("TRANSIT_FEED_STALE_AFTER_SECONDS", "90")))
    parser.add_argument("--feed-timezone", default=os.getenv("TRANSIT_FEED_TIMEZONE", adapter.timezone_name))
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
    args = build_parser().parse_args()
    adapter = get_transit_agency_adapter(str(args.agency))
    cfg = TransitIngestConfig(
        redis_url=str(args.redis),
        interval_seconds=max(1.0, float(args.interval)),
        history_retention=max(12, int(args.history_retention)),
        history_interval_seconds=max(0.0, float(args.history_interval)),
        materialize_read_models=bool(args.read_models),
        read_model_scorecard_limit=max(1, int(args.read_model_scorecard_limit)),
        read_model_trends_limit=max(1, int(args.read_model_trends_limit)),
        read_model_trends_window=max(1, int(args.read_model_trends_window)),
        profile_enabled=bool(args.profile),
        evidence_root=Path(args.evidence_root).expanduser() if str(args.evidence_root).strip() else None,
        runtime=TransitRuntimeConfig(
            system_name=str(args.system_name or adapter.system_name),
            agency_key=adapter.key,
            static_feed=_optional_path(args.static_feed),
            vehicle_positions_feed=_optional_path(args.vehicle_positions_feed),
            trip_updates_feed=_optional_path(args.trip_updates_feed),
            alerts_feed=_optional_path(args.alerts_feed),
            event_overlays_feed=_optional_path(args.event_overlays_feed),
            stale_after_seconds=max(30, int(args.stale_after_seconds)),
            feed_timezone=str(args.feed_timezone or adapter.timezone_name),
        ),
    )
    service = TransitIngestService(cfg)

    def _handle_signal(signum: int, _frame: Any) -> None:
        logger.info("received signal %s, stopping transit ingest", signum)
        service.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.once:
        service.run_once()
        return 0
    service.run()
    return 0


def _optional_path(value: str | None) -> Optional[str]:
    value = str(value or "").strip()
    return value or None


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _load_current_manifest(static_feed: Optional[str]) -> Dict[str, Any]:
    if not static_feed:
        return {}
    current_dir = Path(static_feed).expanduser().resolve().parent
    manifest_path = current_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
