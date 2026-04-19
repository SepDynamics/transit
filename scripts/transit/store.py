"""Valkey storage wrapper for Transit Sentinel runtime payloads."""

from __future__ import annotations

import copy
import json
import os
import time
import threading
import math
from collections import Counter
from typing import Any, Dict, List, Optional
import random

try:
    import redis  # type: ignore
    from redis.exceptions import ConnectionError, TimeoutError, BusyLoadingError
except ImportError:  # pragma: no cover
    redis = None
    ConnectionError = Exception
    TimeoutError = Exception
    BusyLoadingError = Exception

from scripts.shared.runtime import isoformat_ms, scope_matches
from scripts.transit.severity import (
    SEVERITY_COLOR,
    SEVERITY_LABELS,
    build_route_status,
    classify_network_severity,
    severity_rank,
)
from scripts.transit.transit_types import (
    TransitCorridorSnapshot,
    TransitFeedStatus,
    TransitIncidentRecord,
    TransitRegimeRecord,
    TransitReplayTrace,
    TransitVehicleSnapshot,
)

SNAPSHOT_PARTS = ("health", "entities", "regimes", "incidents", "feed_status", "errors")
LIVE_READ_MODEL_PARTS = ("scorecard", "trends", "dashboard", "status:network")


class TransitStore:
    """Persist latest transit payloads plus rolling per-vehicle history."""

    def __init__(self, redis_url: Optional[str] = None, client: Any = None) -> None:
        self._json_cache_ttl = _float_env("TRANSIT_STORE_READ_CACHE_TTL_SECONDS", 5.0)
        self._json_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}
        self._json_cache_lock = threading.RLock()
        # Circuit breaker state
        self._failure_count = 0
        self._last_failure_time = None
        self._circuit_open = False
        self._failure_threshold = 5
        self._recovery_timeout = 30  # seconds
        if client is not None:
            self.client = client
            return
        if redis is None:
            raise RuntimeError("redis dependency is not installed")
        url = redis_url or os.getenv("VALKEY_URL") or os.getenv("REDIS_URL")
        if not url:
            raise RuntimeError("VALKEY_URL or REDIS_URL must be configured")
        self.client = redis.from_url(url, decode_responses=True)
        # Test connection on initialization
        try:
            self.client.ping()
        except (redis.ConnectionError, redis.TimeoutError) as e:
            raise RuntimeError(f"Failed to connect to Redis: {e}") from e

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if self._last_failure_time is None:
            return True
        return time.time() - self._last_failure_time >= self._recovery_timeout

    def _record_success(self) -> None:
        """Record a successful operation."""
        self._failure_count = 0
        self._circuit_open = False

    def _record_failure(self) -> None:
        """Record a failed operation and potentially open circuit."""
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= self._failure_threshold:
            self._circuit_open = True

    def _execute_with_retry(self, operation, *args, max_retries=3, **kwargs):
        """Execute a Redis operation with retry logic and circuit breaker."""
        # Check circuit breaker
        if self._circuit_open:
            if not self._should_attempt_reset():
                raise RuntimeError("Redis circuit breaker is open")
            # Half-open state: allow one attempt to test if service is back

        last_exception = None
        for attempt in range(max_retries):
            try:
                result = operation(*args, **kwargs)
                self._record_success()
                return result
            except (
                redis.ConnectionError,
                redis.TimeoutError,
                redis.BusyLoadingError,
            ) as e:
                last_exception = e
                if attempt < max_retries - 1:  # Don't sleep on the last attempt
                    # Exponential backoff with jitter
                    sleep_time = (2**attempt) + random.uniform(0, 1)
                    time.sleep(min(sleep_time, 10))  # Cap at 10 seconds
            except Exception as e:
                # For non-connection errors, don't retry
                raise e

        # All retries exhausted
        self._record_failure()
        raise last_exception

    def write_snapshot(
        self,
        payload: Dict[str, Any],
        *,
        configured_feeds: Optional[Dict[str, bool]] = None,
        retention: int = 720,
        source: Optional[str] = None,
        trace_id: Optional[str] = None,
        write_history: bool = True,
    ) -> Dict[str, Any]:
        health = copy.deepcopy(dict(payload.get("health") or {}))
        entities = copy.deepcopy(dict(payload.get("entities") or {}))
        regimes = copy.deepcopy(dict(payload.get("regimes") or {}))
        incidents = copy.deepcopy(dict(payload.get("incidents") or {}))
        feed_status = dict(payload.get("feed_status") or {})
        errors = list(payload.get("errors") or [])
        snapshot_source = str(source or self._infer_snapshot_source(payload) or "live")
        snapshot_trace_id = (
            str(trace_id or self._infer_snapshot_trace_id(payload) or "").strip()
            or None
        )
        snapshot_timestamp_ms = self._infer_snapshot_timestamp_ms(payload)
        self._apply_snapshot_context(
            health,
            entities,
            regimes,
            incidents,
            source=snapshot_source,
            trace_id=snapshot_trace_id,
            timestamp_ms=snapshot_timestamp_ms,
        )
        feed_status = TransitFeedStatus.from_mapping(feed_status).to_json()
        if isinstance(health.get("feed_status"), dict):
            health["feed_status"] = TransitFeedStatus.from_mapping(
                health["feed_status"]
            ).to_json()
        entities = self._normalize_entities_payload(entities)
        regimes["regimes"] = [
            TransitRegimeRecord.from_mapping(row).to_json()
            for row in (regimes.get("regimes") or [])
            if isinstance(row, dict)
        ]
        incidents["incidents"] = [
            TransitIncidentRecord.from_mapping(row).to_json()
            for row in (incidents.get("incidents") or [])
            if isinstance(row, dict)
        ]
        corridor_regimes_by_entity = {
            str(row.get("entity_id") or ""): dict(row)
            for row in (regimes.get("regimes") or [])
            if isinstance(row, dict)
        }

        # Use pipeline for batch operations
        def _execute_pipeline():
            pipe = self.client.pipeline()
            pipe.set("transit:health:last", self._dumps(health))
            pipe.set("transit:entities:last", self._dumps(entities))
            pipe.set("transit:regimes:last", self._dumps(regimes))
            pipe.set("transit:incidents:last", self._dumps(incidents))
            pipe.set("transit:feed_status:last", self._dumps(feed_status))
            pipe.set("transit:errors:last", self._dumps({"errors": errors}))

            self._write_latest_snapshot_part(
                pipe,
                "health",
                health,
                source=snapshot_source,
                trace_id=snapshot_trace_id,
                timestamp_ms=snapshot_timestamp_ms,
            )
            self._write_latest_snapshot_part(
                pipe,
                "entities",
                entities,
                source=snapshot_source,
                trace_id=snapshot_trace_id,
                timestamp_ms=snapshot_timestamp_ms,
            )
            self._write_latest_snapshot_part(
                pipe,
                "regimes",
                regimes,
                source=snapshot_source,
                trace_id=snapshot_trace_id,
                timestamp_ms=snapshot_timestamp_ms,
            )
            self._write_latest_snapshot_part(
                pipe,
                "incidents",
                incidents,
                source=snapshot_source,
                trace_id=snapshot_trace_id,
                timestamp_ms=snapshot_timestamp_ms,
            )
            self._write_latest_snapshot_part(
                pipe,
                "feed_status",
                feed_status,
                source=snapshot_source,
                trace_id=snapshot_trace_id,
                timestamp_ms=snapshot_timestamp_ms,
            )
            self._write_latest_snapshot_part(
                pipe,
                "errors",
                {"errors": errors},
                source=snapshot_source,
                trace_id=snapshot_trace_id,
                timestamp_ms=snapshot_timestamp_ms,
            )
            if configured_feeds is not None:
                pipe.set(
                    self.configured_feeds_key(), self._dumps(dict(configured_feeds))
                )

            # Execute the pipeline
            pipe.execute()

        self._execute_with_retry(_execute_pipeline)
        self._clear_json_cache()
        self._write_sources_last()
        snapshot_parts = {
            "source": snapshot_source,
            "trace_id": snapshot_trace_id,
            "timestamp_ms": snapshot_timestamp_ms,
            "health": copy.deepcopy(health),
            "entities": copy.deepcopy(entities),
            "regimes": copy.deepcopy(regimes),
            "incidents": copy.deepcopy(incidents),
            "feed_status": copy.deepcopy(feed_status),
            "errors": {"errors": list(errors)},
        }

        if not write_history:
            return snapshot_parts

        history_ttl_seconds = self._history_ttl_seconds(retention)

        def _write_history_pipeline():
            pipe = self.client.pipeline()
            for vehicle in entities.get("vehicles") or []:
                if not isinstance(vehicle, dict):
                    continue
                entity_id = str(vehicle.get("entity_id") or "").strip()
                if not entity_id:
                    continue
                pipe.set(
                    self.vehicle_meta_key(entity_id, trace_id=snapshot_trace_id),
                    self._dumps(vehicle),
                )
                if snapshot_trace_id:
                    pipe.sadd(
                        self.trace_vehicle_entities_key(snapshot_trace_id), entity_id
                    )

                observation = dict(vehicle.get("observation") or {})
                if observation:
                    history_key = self.observation_history_key(entity_id)
                    pipe.zadd(
                        history_key,
                        {
                            self._dumps(observation): int(
                                observation.get("timestamp_ms") or 0
                            )
                        },
                    )
                    self._pipe_trim_sorted_set(
                        pipe,
                        history_key,
                        retention,
                        ttl_seconds=history_ttl_seconds,
                    )

                regime = dict(vehicle.get("regime") or {})
                if regime:
                    history_key = self.vehicle_regime_history_key(entity_id)
                    pipe.zadd(
                        history_key,
                        {
                            self._dumps(regime): int(
                                regime.get("timestamp_ms")
                                or observation.get("timestamp_ms")
                                or 0
                            )
                        },
                    )
                    self._pipe_trim_sorted_set(
                        pipe,
                        history_key,
                        retention,
                        ttl_seconds=history_ttl_seconds,
                    )

            for regime in regimes.get("regimes") or []:
                if not isinstance(regime, dict):
                    continue
                entity_id = str(regime.get("entity_id") or "").strip()
                if not entity_id:
                    continue
                history_key = self.corridor_regime_history_key(entity_id)
                pipe.zadd(
                    history_key,
                    {self._dumps(regime): int(regime.get("timestamp_ms") or 0)},
                )
                self._pipe_trim_sorted_set(
                    pipe,
                    history_key,
                    retention,
                    ttl_seconds=history_ttl_seconds,
                )

            for line in entities.get("lines") or []:
                if not isinstance(line, dict):
                    continue
                entity_id = str(line.get("entity_id") or "").strip()
                if not entity_id:
                    continue
                merged_line = {
                    **line,
                    "timestamp_ms": int(
                        line.get("timestamp_ms")
                        or corridor_regimes_by_entity.get(entity_id, {}).get(
                            "timestamp_ms"
                        )
                        or int(time.time() * 1000)
                    ),
                    "source": str(
                        line.get("source")
                        or corridor_regimes_by_entity.get(entity_id, {}).get("source")
                        or "live"
                    ),
                    "collection_source": str(
                        line.get("collection_source")
                        or corridor_regimes_by_entity.get(entity_id, {}).get(
                            "collection_source"
                        )
                        or "gtfs_rt"
                    ),
                    "trace_id": line.get(
                        "trace_id",
                        corridor_regimes_by_entity.get(entity_id, {}).get("trace_id"),
                    ),
                }
                pipe.set(
                    self.corridor_meta_key(entity_id, trace_id=snapshot_trace_id),
                    self._dumps(merged_line),
                )
                if snapshot_trace_id:
                    pipe.sadd(
                        self.trace_corridor_entities_key(snapshot_trace_id), entity_id
                    )
                history_key = self.corridor_summary_history_key(entity_id)
                pipe.zadd(
                    history_key,
                    {
                        self._dumps(merged_line): int(
                            merged_line.get("timestamp_ms") or 0
                        )
                    },
                )
                self._pipe_trim_sorted_set(
                    pipe,
                    history_key,
                    retention,
                    ttl_seconds=history_ttl_seconds,
                )

            for incident in incidents.get("incidents") or []:
                if not isinstance(incident, dict):
                    continue
                entity_id = str(incident.get("entity_id") or "").strip()
                if not entity_id:
                    continue
                history_key = self.corridor_incident_history_key(entity_id)
                pipe.zadd(
                    history_key,
                    {self._dumps(incident): int(incident.get("timestamp_ms") or 0)},
                )
                self._pipe_trim_sorted_set(
                    pipe,
                    history_key,
                    retention,
                    ttl_seconds=history_ttl_seconds,
                )

            pipe.execute()

        self._execute_with_retry(_write_history_pipeline)
        self._clear_json_cache()
        return snapshot_parts

    def write_replay_trace(self, trace: TransitReplayTrace) -> None:
        payload = trace.to_json()

        def _set_trace_meta():
            self.client.set(self.trace_meta_key(trace.trace_id), self._dumps(payload))

        def _sadd_trace_ids():
            self.client.sadd("transit:trace_ids", trace.trace_id)

        self._execute_with_retry(_set_trace_meta)
        self._clear_json_cache(self.trace_meta_key(trace.trace_id))
        self._execute_with_retry(_sadd_trace_ids)

        if trace.latest_snapshot_timestamp_ms is not None:

            def _zadd_trace_timestamps():
                self.client.zadd(
                    "transit:trace_timestamps",
                    {trace.trace_id: int(trace.latest_snapshot_timestamp_ms)},
                )

            self._execute_with_retry(_zadd_trace_timestamps)

        def _set_sources_last():
            self.client.set("transit:sources:last", self._dumps(self.sources()))

        self._execute_with_retry(_set_sources_last)
        self._clear_json_cache("transit:sources:last")

    def write_status(self, key: str, payload: Dict[str, Any]) -> None:
        def _set_status():
            self.client.set(key, self._dumps(payload))

        self._execute_with_retry(_set_status)
        self._clear_json_cache(key)

    def read_status(self, key: str) -> Dict[str, Any]:
        return self.read_json_key(key, default={})

    def write_live_read_models(
        self,
        *,
        scorecard_limit: int = 60,
        trends_limit: int = 6,
        trends_window: int = 24,
        include_scorecard: bool = True,
        include_trends: bool = True,
        include_dashboard: bool = True,
        include_status_network: bool = True,
        snapshot_parts: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Materialize live read models used by low-latency API paths."""
        scorecard_limit = max(1, int(scorecard_limit or 1))
        trends_limit = max(1, int(trends_limit or 1))
        trends_window = max(1, int(trends_window or 1))
        generated_at = isoformat_ms()
        payloads: Dict[str, Dict[str, Any]] = {}

        if include_scorecard:
            payloads["scorecard"] = self._with_read_model_metadata(
                self.scorecard(scope="live", limit=scorecard_limit),
                kind="scorecard",
                generated_at=generated_at,
                limit=scorecard_limit,
            )

        if include_trends:
            trends_payload = self._with_read_model_metadata(
                self.trends(scope="live", limit=trends_limit, window=trends_window),
                kind="trends",
                generated_at=generated_at,
                limit=trends_limit,
                window=trends_window,
            )
            payloads["trends"] = trends_payload
        elif include_dashboard:
            trends_payload = self.read_live_read_model("trends")
            if not trends_payload:
                trends_payload = self._with_read_model_metadata(
                    self.trends(
                        scope="live", limit=trends_limit, window=trends_window
                    ),
                    kind="trends",
                    generated_at=generated_at,
                    limit=trends_limit,
                    window=trends_window,
                )
        else:
            trends_payload = {}

        health_payload: Dict[str, Any] = {}
        entities_payload: Dict[str, Any] = {}
        regimes_payload: Dict[str, Any] = {}
        incidents_payload: Dict[str, Any] = {}
        if include_dashboard or include_status_network:
            if _snapshot_parts_are_live(snapshot_parts):
                health_payload = copy.deepcopy(dict(snapshot_parts.get("health") or {}))
                entities_payload = copy.deepcopy(
                    dict(snapshot_parts.get("entities") or {})
                )
                regimes_payload = copy.deepcopy(
                    dict(snapshot_parts.get("regimes") or {})
                )
                incidents_payload = copy.deepcopy(
                    dict(snapshot_parts.get("incidents") or {})
                )
            else:
                health_payload = self.health(scope="live")
                entities_payload = self.entities(scope="live")
                regimes_payload = self.regimes(scope="live")
                incidents_payload = self.incidents(scope="live")

        if include_dashboard:
            if not trends_payload:
                trends_payload = self.read_live_read_model("trends")
            payloads["dashboard"] = self._with_read_model_metadata(
                {
                    "generated_at": generated_at,
                    "scope": "live",
                    "trace_id": health_payload.get("trace_id"),
                    "health": health_payload,
                    "entities": _dashboard_entities_read_model(entities_payload),
                    "regimes": regimes_payload,
                    "incidents": incidents_payload,
                    "trends": trends_payload
                    if trends_payload
                    else self.trends(
                        scope="live", limit=trends_limit, window=trends_window
                    ),
                },
                kind="dashboard",
                generated_at=generated_at,
                trends_limit=trends_limit,
                trends_window=trends_window,
            )

        if include_status_network:
            payloads["status:network"] = self._with_read_model_metadata(
                _public_status_network_read_model(
                    health_payload,
                    entities_payload,
                    regimes_payload,
                    incidents_payload,
                    generated_at=generated_at,
                ),
                kind="status:network",
                generated_at=generated_at,
            )

        def _write_pipeline():
            pipe = self.client.pipeline()
            for kind, payload in payloads.items():
                pipe.set(self.live_read_model_key(kind), self._dumps(payload))
            pipe.execute()

        if payloads:
            self._execute_with_retry(_write_pipeline)
            self._clear_json_cache(
                *[self.live_read_model_key(kind) for kind in payloads]
            )
        return payloads

    def read_live_read_model(self, kind: str) -> Dict[str, Any]:
        if kind not in LIVE_READ_MODEL_PARTS:
            return {}
        return self.read_json_key(self.live_read_model_key(kind), default={})

    def health(
        self, *, scope: str = "all", trace_id: str | None = None
    ) -> Dict[str, Any]:
        resolved_trace_id = self._resolve_trace_id(scope=scope, trace_id=trace_id)
        payload = self._read_latest_snapshot_part(
            "health", scope=scope, trace_id=trace_id, default=self._default_health()
        )
        return {**payload, "scope": scope, "trace_id": resolved_trace_id}

    def entities(
        self, *, scope: str = "all", trace_id: str | None = None
    ) -> Dict[str, Any]:
        resolved_trace_id = self._resolve_trace_id(scope=scope, trace_id=trace_id)
        payload = self._read_latest_snapshot_part(
            "entities", scope=scope, trace_id=trace_id, default=self._default_entities()
        )
        lines = [
            TransitCorridorSnapshot.from_mapping(row).to_json()
            for row in (payload.get("lines") or [])
            if isinstance(row, dict)
            and scope_matches(row, scope)
            and (
                resolved_trace_id in (None, "")
                or str(row.get("trace_id") or "") == resolved_trace_id
            )
        ]
        active_lines = [
            TransitCorridorSnapshot.from_mapping(row).to_json()
            for row in (payload.get("active_lines") or [])
            if isinstance(row, dict)
            and scope_matches(row, scope)
            and (
                resolved_trace_id in (None, "")
                or str(row.get("trace_id") or "") == resolved_trace_id
            )
        ]
        scheduled_later_lines = [
            TransitCorridorSnapshot.from_mapping(row).to_json()
            for row in (payload.get("scheduled_later_lines") or [])
            if isinstance(row, dict)
            and scope_matches(row, scope)
            and (
                resolved_trace_id in (None, "")
                or str(row.get("trace_id") or "") == resolved_trace_id
            )
        ]
        inactive_lines = [
            TransitCorridorSnapshot.from_mapping(row).to_json()
            for row in (payload.get("inactive_lines") or [])
            if isinstance(row, dict)
            and scope_matches(row, scope)
            and (
                resolved_trace_id in (None, "")
                or str(row.get("trace_id") or "") == resolved_trace_id
            )
        ]
        vehicles = [
            TransitVehicleSnapshot.from_mapping(row).to_json()
            for row in (payload.get("vehicles") or [])
            if isinstance(row, dict)
            and scope_matches(row.get("observation") or row, scope)
            and (
                resolved_trace_id in (None, "")
                or str((row.get("observation") or row).get("trace_id") or "")
                == resolved_trace_id
            )
        ]
        lines.sort(key=self._corridor_sort_key)
        active_lines.sort(key=self._corridor_sort_key)
        scheduled_later_lines.sort(key=self._corridor_sort_key)
        inactive_lines.sort(key=self._corridor_sort_key)
        return {
            **payload,
            "scope": scope,
            "trace_id": resolved_trace_id,
            "lines": lines,
            "active_lines": active_lines,
            "scheduled_later_lines": scheduled_later_lines,
            "inactive_lines": inactive_lines,
            "vehicles": vehicles,
        }

    def regimes(
        self, *, scope: str = "all", trace_id: str | None = None
    ) -> Dict[str, Any]:
        resolved_trace_id = self._resolve_trace_id(scope=scope, trace_id=trace_id)
        payload = self._read_latest_snapshot_part(
            "regimes", scope=scope, trace_id=trace_id, default=self._default_regimes()
        )
        regimes = [
            row
            for row in (payload.get("regimes") or [])
            if isinstance(row, dict)
            and scope_matches(row, scope)
            and (
                resolved_trace_id in (None, "")
                or str(row.get("trace_id") or "") == resolved_trace_id
            )
        ]
        recurring = (
            list(payload.get("recurring_regimes") or [])
            if resolved_trace_id in (None, "") and scope in ("", "all", "live", None)
            else []
        )
        return {
            **payload,
            "scope": scope,
            "trace_id": resolved_trace_id,
            "regimes": regimes,
            "recurring_regimes": recurring,
        }

    def incidents(
        self, *, scope: str = "all", trace_id: str | None = None
    ) -> Dict[str, Any]:
        resolved_trace_id = self._resolve_trace_id(scope=scope, trace_id=trace_id)
        payload = self._read_latest_snapshot_part(
            "incidents",
            scope=scope,
            trace_id=trace_id,
            default=self._default_incidents(),
        )
        incidents = [
            TransitIncidentRecord.from_mapping(row).to_json()
            for row in (payload.get("incidents") or [])
            if isinstance(row, dict)
            and scope_matches(row, scope)
            and (
                resolved_trace_id in (None, "")
                or str(row.get("trace_id") or "") == resolved_trace_id
            )
        ]
        incidents.sort(key=self._incident_sort_key)
        return {
            **payload,
            "scope": scope,
            "trace_id": resolved_trace_id,
            "incidents": incidents,
        }

    def sources(self) -> Dict[str, Any]:
        replay_enabled = self._replay_enabled()
        traces = self.list_replay_traces() if replay_enabled else []
        trace_ids = [
            str(row.get("trace_id") or "") for row in traces if row.get("trace_id")
        ]
        live_health = self.read_json_key(self.live_payload_key("health"), default={})
        configured_feeds = self.read_json_key(self.configured_feeds_key(), default={})
        live_feed_status = live_health.get("feed_status") or {}
        has_live = bool(
            int(live_health.get("vehicle_count") or 0) > 0
            or int(live_health.get("visible_line_count") or 0) > 0
            or int(live_health.get("line_count") or 0) > 0
            or int(live_feed_status.get("trip_update_count") or 0) > 0
            or int(live_feed_status.get("alert_count") or 0) > 0
        )
        has_replay = replay_enabled and bool(traces)
        scopes = [{"id": "live", "label": "Live feed"}]
        if replay_enabled:
            scopes = [
                {"id": "all", "label": "All feeds"},
                *scopes,
                {"id": "replay", "label": "Replay"},
            ]
        return {
            "generated_at": isoformat_ms(),
            "scopes": scopes,
            "available": {"live": has_live, "replay": has_replay},
            "configured_feeds": configured_feeds,
            "traces": traces,
            "trace_ids": trace_ids,
        }

    def scorecard(
        self,
        *,
        scope: str = "all",
        trace_id: str | None = None,
        limit: int = 720,
    ) -> Dict[str, Any]:
        """Return a rolling KPI scorecard across all tracked corridors.

        Aggregates from per-corridor history stored in the rolling Valkey store.
        Provides the basis for weekly/monthly service reliability reports.
        """
        resolved_trace_id = self._resolve_trace_id(scope=scope, trace_id=trace_id)
        entities = self.entities(scope=scope, trace_id=resolved_trace_id)

        corridor_scorecards: List[Dict[str, Any]] = []
        network_hazard_samples: List[float] = []
        network_delay_samples: List[int] = []
        total_incidents = 0
        total_control_snapshots = 0
        total_snapshots = 0
        regime_totals: Counter[str] = Counter()
        action_totals: Counter[str] = Counter()

        corridor_lines: List[tuple[str, Dict[str, Any]]] = []
        for line in entities.get("lines") or []:
            if not isinstance(line, dict):
                continue
            entity_id = str(line.get("entity_id") or "").strip()
            if not entity_id:
                continue
            corridor_lines.append((entity_id, line))

        raw_histories: List[Any] = []
        if corridor_lines:
            pipe = self.client.pipeline()
            for entity_id, _line in corridor_lines:
                pipe.zrange(self.corridor_summary_history_key(entity_id), -limit, -1)
                pipe.zrange(self.corridor_regime_history_key(entity_id), -limit, -1)
                pipe.zrange(self.corridor_incident_history_key(entity_id), -limit, -1)
            raw_histories = pipe.execute()

        for index, (entity_id, line) in enumerate(corridor_lines):
            history_offset = index * 3
            summary_rows = raw_histories[history_offset] if raw_histories else []
            regime_rows = raw_histories[history_offset + 1] if raw_histories else []
            incident_rows = raw_histories[history_offset + 2] if raw_histories else []

            summaries: List[Dict[str, Any]] = []
            for payload in (self._loads(row) for row in summary_rows):
                if not payload:
                    continue
                row = TransitCorridorSnapshot.from_mapping(payload).to_json()
                if scope_matches(row, scope) and (
                    resolved_trace_id in (None, "")
                    or str(row.get("trace_id") or "") == resolved_trace_id
                ):
                    summaries.append(row)

            regimes: List[Dict[str, Any]] = []
            for payload in (self._loads(row) for row in regime_rows):
                if not payload:
                    continue
                row = TransitRegimeRecord.from_mapping(payload).to_json()
                if scope_matches(row, scope) and (
                    resolved_trace_id in (None, "")
                    or str(row.get("trace_id") or "") == resolved_trace_id
                ):
                    regimes.append(row)

            incidents: List[Dict[str, Any]] = []
            for payload in (self._loads(row) for row in incident_rows):
                if not payload:
                    continue
                row = TransitIncidentRecord.from_mapping(payload).to_json()
                if scope_matches(row, scope) and (
                    resolved_trace_id in (None, "")
                    or str(row.get("trace_id") or "") == resolved_trace_id
                ):
                    incidents.append(row)

            if not summaries and not regimes:
                continue

            # Hazard
            hazard_values = [
                float(row.get("avg_hazard") or row.get("hazard") or 0.0)
                for row in summaries
            ]
            avg_hazard = (
                round(sum(hazard_values) / len(hazard_values), 4)
                if hazard_values
                else 0.0
            )
            max_hazard = round(max(hazard_values or [0.0]), 4)
            hazard_p90 = round(
                sorted(hazard_values)[int(len(hazard_values) * 0.9)]
                if hazard_values
                else 0.0,
                4,
            )
            network_hazard_samples.extend(hazard_values)

            # Delay
            delay_values = [
                int(row.get("median_delay_seconds") or 0) for row in summaries
            ]
            avg_delay = (
                round(sum(delay_values) / len(delay_values)) if delay_values else 0
            )
            max_delay = max(delay_values or [0])
            network_delay_samples.extend(delay_values)

            # On-time proxy: snapshots where median delay < 120s (2 min threshold)
            on_time_count = sum(1 for d in delay_values if d < 120)
            on_time_pct = (
                round(100.0 * on_time_count / len(delay_values), 1)
                if delay_values
                else 100.0
            )

            # Regimes
            regime_names = [
                str(row.get("regime") or "") for row in regimes if row.get("regime")
            ]
            regime_counts: Counter[str] = Counter(regime_names)
            regime_totals.update(regime_names)
            healthy_pct = (
                round(100.0 * regime_counts.get("healthy", 0) / len(regime_names), 1)
                if regime_names
                else 100.0
            )
            unstable_pct = (
                round(
                    100.0
                    * sum(
                        regime_counts.get(r, 0)
                        for r in (
                            "corridor_unstable",
                            "headway_collapse",
                            "bunching_onset",
                            "service_degraded",
                        )
                    )
                    / len(regime_names),
                    1,
                )
                if regime_names
                else 0.0
            )

            # Actions
            action_names = [
                str(row.get("top_action") or row.get("action") or "")
                for row in summaries
                if row.get("top_action") or row.get("action")
            ]
            action_counts_corridor: Counter[str] = Counter(action_names)
            action_totals.update(action_names)

            # Incidents
            incident_count = len(incidents)
            total_incidents += incident_count

            snapshot_count = len(summaries)
            total_snapshots += snapshot_count
            control_count = sum(
                1
                for row in summaries
                if (row.get("activity_status") or "").lower()
                in ("no_service", "scheduled_later", "inactive")
            )
            total_control_snapshots += control_count

            corridor_scorecards.append(
                {
                    "entity_id": entity_id,
                    "label": str(line.get("label") or entity_id),
                    "route_id": line.get("route_id"),
                    "snapshot_count": snapshot_count,
                    "incident_count": incident_count,
                    "avg_hazard": avg_hazard,
                    "max_hazard": max_hazard,
                    "hazard_p90": hazard_p90,
                    "avg_delay_seconds": avg_delay,
                    "max_delay_seconds": max_delay,
                    "on_time_pct": on_time_pct,
                    "healthy_pct": healthy_pct,
                    "unstable_pct": unstable_pct,
                    "top_regime": regime_counts.most_common(1)[0][0]
                    if regime_counts
                    else "healthy",
                    "top_action": action_counts_corridor.most_common(1)[0][0]
                    if action_counts_corridor
                    else "monitor",
                    "regime_counts": dict(sorted(regime_counts.items())),
                    "action_counts": dict(sorted(action_counts_corridor.items())),
                }
            )

        corridor_scorecards.sort(
            key=lambda row: (
                -float(row.get("avg_hazard") or 0.0),
                -int(row.get("incident_count") or 0),
            )
        )

        # Network-level aggregates
        net_avg_hazard = (
            round(sum(network_hazard_samples) / len(network_hazard_samples), 4)
            if network_hazard_samples
            else 0.0
        )
        net_avg_delay = (
            round(sum(network_delay_samples) / len(network_delay_samples))
            if network_delay_samples
            else 0
        )
        net_on_time = (
            round(
                100.0
                * sum(1 for d in network_delay_samples if d < 120)
                / len(network_delay_samples),
                1,
            )
            if network_delay_samples
            else 100.0
        )
        net_regime_count = sum(regime_totals.values())
        net_healthy_pct = (
            round(100.0 * regime_totals.get("healthy", 0) / net_regime_count, 1)
            if net_regime_count
            else 100.0
        )
        net_unstable_pct = (
            round(
                100.0
                * sum(
                    regime_totals.get(r, 0)
                    for r in (
                        "corridor_unstable",
                        "headway_collapse",
                        "bunching_onset",
                        "service_degraded",
                    )
                )
                / net_regime_count,
                1,
            )
            if net_regime_count
            else 0.0
        )
        corridor_count = len(corridor_scorecards)
        unstable_corridor_count = sum(
            1
            for row in corridor_scorecards
            if float(row.get("unstable_pct") or 0.0) >= 20.0
        )

        return {
            "generated_at": isoformat_ms(),
            "scope": scope,
            "trace_id": resolved_trace_id,
            "window_snapshots": total_snapshots,
            "corridor_count": corridor_count,
            "total_incidents": total_incidents,
            "network": {
                "avg_hazard": net_avg_hazard,
                "avg_delay_seconds": net_avg_delay,
                "on_time_pct": net_on_time,
                "healthy_pct": net_healthy_pct,
                "unstable_pct": net_unstable_pct,
                "unstable_corridor_count": unstable_corridor_count,
                "top_regimes": dict(regime_totals.most_common(6)),
                "top_actions": dict(action_totals.most_common(6)),
            },
            "corridors": corridor_scorecards,
        }

    def trends(
        self,
        *,
        scope: str = "all",
        trace_id: str | None = None,
        limit: int = 6,
        window: int = 24,
    ) -> Dict[str, Any]:
        resolved_trace_id = self._resolve_trace_id(scope=scope, trace_id=trace_id)
        entities = self.entities(scope=scope, trace_id=resolved_trace_id)
        corridors: List[Dict[str, Any]] = []
        recent_action_counts: Counter[str] = Counter()
        recent_regime_counts: Counter[str] = Counter()
        recent_incident_total = 0

        corridor_lines: List[tuple[str, Dict[str, Any]]] = []
        for line in entities.get("lines") or []:
            if not isinstance(line, dict):
                continue
            entity_id = str(line.get("entity_id") or "").strip()
            if not entity_id:
                continue
            corridor_lines.append((entity_id, line))

        raw_histories: List[Any] = []
        if corridor_lines:
            pipe = self.client.pipeline()
            for entity_id, _line in corridor_lines:
                pipe.zrange(self.corridor_summary_history_key(entity_id), -window, -1)
                pipe.zrange(self.corridor_regime_history_key(entity_id), -window, -1)
                pipe.zrange(self.corridor_incident_history_key(entity_id), -window, -1)
            raw_histories = pipe.execute()

        for index, (entity_id, line) in enumerate(corridor_lines):
            history_offset = index * 3
            summary_rows = raw_histories[history_offset] if raw_histories else []
            regime_rows = raw_histories[history_offset + 1] if raw_histories else []
            incident_rows = raw_histories[history_offset + 2] if raw_histories else []

            summaries: List[Dict[str, Any]] = []
            for payload in (self._loads(row) for row in summary_rows):
                if not payload:
                    continue
                row = TransitCorridorSnapshot.from_mapping(payload).to_json()
                if scope_matches(row, scope) and (
                    resolved_trace_id in (None, "")
                    or str(row.get("trace_id") or "") == resolved_trace_id
                ):
                    summaries.append(row)

            regimes: List[Dict[str, Any]] = []
            for payload in (self._loads(row) for row in regime_rows):
                if not payload:
                    continue
                row = TransitRegimeRecord.from_mapping(payload).to_json()
                if scope_matches(row, scope) and (
                    resolved_trace_id in (None, "")
                    or str(row.get("trace_id") or "") == resolved_trace_id
                ):
                    regimes.append(row)

            incidents: List[Dict[str, Any]] = []
            for payload in (self._loads(row) for row in incident_rows):
                if not payload:
                    continue
                row = TransitIncidentRecord.from_mapping(payload).to_json()
                if scope_matches(row, scope) and (
                    resolved_trace_id in (None, "")
                    or str(row.get("trace_id") or "") == resolved_trace_id
                ):
                    incidents.append(row)
            if not summaries and not regimes and not incidents:
                continue

            latest_summary = summaries[-1] if summaries else dict(line)
            hazard_series = [
                round(float(row.get("avg_hazard") or row.get("hazard") or 0.0), 4)
                for row in summaries[-12:]
            ]
            delay_series = [
                int(row.get("median_delay_seconds") or 0) for row in summaries[-12:]
            ]
            snapshot_actions = [
                str(row.get("top_action") or "")
                for row in summaries
                if row.get("top_action")
            ]
            regime_names = [
                str(row.get("regime") or "") for row in regimes if row.get("regime")
            ]
            top_regime = (
                Counter(regime_names).most_common(1)[0][0]
                if regime_names
                else str(latest_summary.get("top_regime") or "healthy")
            )
            latest_action = str(
                latest_summary.get("top_action")
                or (snapshot_actions[-1] if snapshot_actions else "monitor")
            )
            latest_hazard = round(
                float(
                    latest_summary.get("avg_hazard")
                    or latest_summary.get("hazard")
                    or 0.0
                ),
                4,
            )
            avg_hazard = (
                round(sum(hazard_series) / len(hazard_series), 4)
                if hazard_series
                else latest_hazard
            )
            incident_count = len(incidents)

            recent_action_counts.update(snapshot_actions[-6:])
            recent_regime_counts.update(regime_names[-6:])
            recent_incident_total += incident_count
            corridors.append(
                {
                    "entity_id": entity_id,
                    "label": str(
                        latest_summary.get("label") or line.get("label") or entity_id
                    ),
                    "route_id": latest_summary.get("route_id"),
                    "snapshot_count": len(summaries),
                    "incident_count": incident_count,
                    "avg_hazard": avg_hazard,
                    "max_hazard": round(max(hazard_series or [latest_hazard]), 4),
                    "latest_hazard": latest_hazard,
                    "latest_action": latest_action,
                    "latest_regime": top_regime,
                    "latest_delay_seconds": latest_summary.get("median_delay_seconds"),
                    "latest_activity_status": latest_summary.get("activity_status"),
                    "hazard_series": hazard_series,
                    "delay_series": delay_series,
                    "recent_actions": [
                        action for action in snapshot_actions[-4:] if action
                    ],
                }
            )

        corridors.sort(
            key=lambda row: (
                -float(row.get("latest_hazard") or 0.0),
                -int(row.get("incident_count") or 0),
                str(row.get("label") or ""),
            )
        )
        return {
            "generated_at": isoformat_ms(),
            "scope": scope,
            "trace_id": resolved_trace_id,
            "summary": {
                "corridor_count": len(corridors),
                "unstable_corridor_count": sum(
                    1
                    for row in corridors
                    if float(row.get("latest_hazard") or 0.0) >= 0.5
                ),
                "recent_incident_count": recent_incident_total,
                "recent_action_counts": dict(sorted(recent_action_counts.items())),
                "recent_regime_counts": dict(sorted(recent_regime_counts.items())),
            },
            "corridors": corridors[: max(1, int(limit or 1))],
        }

    def history(
        self,
        entity_id: str,
        *,
        scope: str = "all",
        trace_id: str | None = None,
        limit: int = 72,
    ) -> Dict[str, Any]:
        resolved_trace_id = self._resolve_trace_id(scope=scope, trace_id=trace_id)
        entities = self.entities(scope=scope, trace_id=resolved_trace_id)
        if entity_id.startswith("route:"):
            observations = [
                row
                for row in self.get_recent_corridor_summaries(entity_id, limit=limit)
                if scope_matches(row, scope)
                and (
                    resolved_trace_id in (None, "")
                    or str(row.get("trace_id") or "") == resolved_trace_id
                )
            ]
            regimes = [
                row
                for row in self.get_recent_corridor_regimes(entity_id, limit=limit)
                if scope_matches(row, scope)
                and (
                    resolved_trace_id in (None, "")
                    or str(row.get("trace_id") or "") == resolved_trace_id
                )
            ]
            incidents = [
                row
                for row in self.get_recent_corridor_incidents(entity_id, limit=limit)
                if scope_matches(row, scope)
                and (
                    resolved_trace_id in (None, "")
                    or str(row.get("trace_id") or "") == resolved_trace_id
                )
            ]
            entity = (
                next(
                    (
                        row
                        for row in (entities.get("lines") or [])
                        if str(row.get("entity_id") or "") == entity_id
                    ),
                    None,
                )
                or self.get_corridor(entity_id, scope=scope, trace_id=resolved_trace_id)
                or {"entity_id": entity_id}
            )
            return {
                "generated_at": isoformat_ms(),
                "scope": scope,
                "trace_id": resolved_trace_id,
                "entity": entity,
                "observations": observations,
                "regimes": regimes,
                "incidents": incidents,
            }

        observations = [
            row
            for row in self.get_recent_observations(entity_id, limit=limit)
            if scope_matches(row, scope)
            and (
                resolved_trace_id in (None, "")
                or str(row.get("trace_id") or "") == resolved_trace_id
            )
        ]
        regimes = [
            row
            for row in self.get_recent_vehicle_regimes(entity_id, limit=limit)
            if scope_matches(row, scope)
            and (
                resolved_trace_id in (None, "")
                or str(row.get("trace_id") or "") == resolved_trace_id
            )
        ]
        entity = (
            next(
                (
                    row
                    for row in (entities.get("vehicles") or [])
                    if str(row.get("entity_id") or "") == entity_id
                ),
                None,
            )
            or self.get_vehicle(entity_id, scope=scope, trace_id=resolved_trace_id)
            or {"entity_id": entity_id}
        )
        return {
            "generated_at": isoformat_ms(),
            "scope": scope,
            "trace_id": resolved_trace_id,
            "entity": entity,
            "observations": observations,
            "regimes": regimes,
            "incidents": [],
        }

    def get_vehicle(
        self, entity_id: str, *, scope: str = "all", trace_id: str | None = None
    ) -> Dict[str, Any]:
        payload = self.read_json_key(
            self.vehicle_meta_key(
                entity_id,
                trace_id=self._resolve_trace_id(scope=scope, trace_id=trace_id),
            ),
            default={},
        )
        return TransitVehicleSnapshot.from_mapping(payload).to_json() if payload else {}

    def get_corridor(
        self, entity_id: str, *, scope: str = "all", trace_id: str | None = None
    ) -> Dict[str, Any]:
        payload = self.read_json_key(
            self.corridor_meta_key(
                entity_id,
                trace_id=self._resolve_trace_id(scope=scope, trace_id=trace_id),
            ),
            default={},
        )
        return (
            TransitCorridorSnapshot.from_mapping(payload).to_json() if payload else {}
        )

    def get_recent_observations(
        self, entity_id: str, *, limit: int = 72
    ) -> List[Dict[str, Any]]:
        rows = (
            self.client.zrange(self.observation_history_key(entity_id), -limit, -1)
            or []
        )
        return [payload for payload in (self._loads(row) for row in rows) if payload]

    def get_recent_vehicle_regimes(
        self, entity_id: str, *, limit: int = 72
    ) -> List[Dict[str, Any]]:
        rows = (
            self.client.zrange(self.vehicle_regime_history_key(entity_id), -limit, -1)
            or []
        )
        return [
            TransitRegimeRecord.from_mapping(payload).to_json()
            for payload in (self._loads(row) for row in rows)
            if payload
        ]

    def get_recent_corridor_summaries(
        self, entity_id: str, *, limit: int = 72
    ) -> List[Dict[str, Any]]:
        rows = (
            self.client.zrange(self.corridor_summary_history_key(entity_id), -limit, -1)
            or []
        )
        return [
            TransitCorridorSnapshot.from_mapping(payload).to_json()
            for payload in (self._loads(row) for row in rows)
            if payload
        ]

    def get_recent_corridor_regimes(
        self, entity_id: str, *, limit: int = 72
    ) -> List[Dict[str, Any]]:
        rows = (
            self.client.zrange(self.corridor_regime_history_key(entity_id), -limit, -1)
            or []
        )
        return [
            TransitRegimeRecord.from_mapping(payload).to_json()
            for payload in (self._loads(row) for row in rows)
            if payload
        ]

    def get_recent_corridor_incidents(
        self, entity_id: str, *, limit: int = 72
    ) -> List[Dict[str, Any]]:
        rows = (
            self.client.zrange(
                self.corridor_incident_history_key(entity_id), -limit, -1
            )
            or []
        )
        return [
            TransitIncidentRecord.from_mapping(payload).to_json()
            for payload in (self._loads(row) for row in rows)
            if payload
        ]

    def list_trace_ids(self) -> List[str]:
        if not self._replay_enabled():
            return []
        ranked = [
            str(value)
            for value in (
                self.client.zrevrange("transit:trace_timestamps", 0, -1) or []
            )
            if value
        ]
        if ranked:
            return ranked
        return sorted(
            str(value)
            for value in (self.client.smembers("transit:trace_ids") or set())
            if value
        )

    def list_replay_traces(self) -> List[Dict[str, Any]]:
        traces: List[Dict[str, Any]] = []
        for trace_id in self.list_trace_ids():
            payload = self.read_json_key(self.trace_meta_key(trace_id), default={})
            if payload:
                traces.append(TransitReplayTrace.from_mapping(payload).to_json())
                continue
            timestamp_ms = self._optional_sorted_set_score(
                "transit:trace_timestamps", trace_id
            )
            traces.append(
                TransitReplayTrace(
                    trace_id=trace_id,
                    latest_snapshot_timestamp_ms=timestamp_ms,
                ).to_json()
            )
        return traces

    def read_json_key(
        self, key: str, *, default: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        now = time.monotonic()
        if self._json_cache_ttl > 0:
            with self._json_cache_lock:
                cached = self._json_cache.get(key)
                if cached and cached[0] >= now:
                    return cached[1]
        raw = self.client.get(key)
        if not raw:
            return dict(default or {})
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return dict(default or {})
        if self._json_cache_ttl > 0:
            with self._json_cache_lock:
                self._json_cache[key] = (now + self._json_cache_ttl, payload)
        return payload

    def _write_sources_last(self) -> None:
        def _set_sources_last():
            self.client.set("transit:sources:last", self._dumps(self.sources()))

        self._execute_with_retry(_set_sources_last)
        self._clear_json_cache("transit:sources:last")

    def _clear_json_cache(self, *keys: str) -> None:
        if self._json_cache_ttl <= 0:
            return
        with self._json_cache_lock:
            if keys:
                for key in keys:
                    self._json_cache.pop(key, None)
                return
            self._json_cache.clear()

    @staticmethod
    def vehicle_meta_key(entity_id: str, *, trace_id: str | None = None) -> str:
        if trace_id:
            return f"transit:trace:{trace_id}:vehicle:meta:{entity_id}"
        return f"transit:vehicle:meta:{entity_id}"

    @staticmethod
    def corridor_meta_key(entity_id: str, *, trace_id: str | None = None) -> str:
        if trace_id:
            return f"transit:trace:{trace_id}:corridor:meta:{entity_id}"
        return f"transit:corridor:meta:{entity_id}"

    @staticmethod
    def live_payload_key(kind: str) -> str:
        return f"transit:live:last:{kind}"

    @staticmethod
    def replay_payload_key(kind: str) -> str:
        return f"transit:replay:last:{kind}"

    @staticmethod
    def trace_payload_key(trace_id: str, kind: str) -> str:
        return f"transit:trace:{trace_id}:last:{kind}"

    @staticmethod
    def trace_meta_key(trace_id: str) -> str:
        return f"transit:trace:{trace_id}:meta"

    @staticmethod
    def configured_feeds_key() -> str:
        return "transit:configured_feeds:last"

    @staticmethod
    def live_read_model_key(kind: str) -> str:
        if kind == "status:network":
            return "transit:status:network:last"
        return f"transit:{kind}:live:last"

    @staticmethod
    def trace_vehicle_entities_key(trace_id: str) -> str:
        return f"transit:trace:{trace_id}:vehicles"

    @staticmethod
    def trace_corridor_entities_key(trace_id: str) -> str:
        return f"transit:trace:{trace_id}:corridors"

    @staticmethod
    def observation_history_key(entity_id: str) -> str:
        return f"transit:vehicle:history:observations:{entity_id}"

    @staticmethod
    def vehicle_regime_history_key(entity_id: str) -> str:
        return f"transit:vehicle:history:regimes:{entity_id}"

    @staticmethod
    def corridor_summary_history_key(entity_id: str) -> str:
        return f"transit:corridor:history:summaries:{entity_id}"

    @staticmethod
    def corridor_regime_history_key(entity_id: str) -> str:
        return f"transit:corridor:history:regimes:{entity_id}"

    @staticmethod
    def corridor_incident_history_key(entity_id: str) -> str:
        return f"transit:corridor:history:incidents:{entity_id}"

    def clear_runtime_state(self) -> int:
        keys: set[str] = set()
        for pattern in ("transit:*", "ops:transit_*"):
            for key in self.client.scan_iter(match=pattern, count=500):
                if key:
                    keys.add(str(key))
        if not keys:
            return 0
        deleted = 0
        key_list = sorted(keys)
        for index in range(0, len(key_list), 500):
            deleted += int(self.client.delete(*key_list[index : index + 500]) or 0)
        return deleted

    def clear_replay_trace(self, trace_id: str) -> None:
        vehicle_ids = sorted(
            str(value)
            for value in (
                self.client.smembers(self.trace_vehicle_entities_key(trace_id)) or set()
            )
            if value
        )
        corridor_ids = sorted(
            str(value)
            for value in (
                self.client.smembers(self.trace_corridor_entities_key(trace_id))
                or set()
            )
            if value
        )
        for entity_id in vehicle_ids:
            self._prune_trace_rows(self.observation_history_key(entity_id), trace_id)
            self._prune_trace_rows(self.vehicle_regime_history_key(entity_id), trace_id)
            self.client.delete(self.vehicle_meta_key(entity_id, trace_id=trace_id))
        for entity_id in corridor_ids:
            self._prune_trace_rows(
                self.corridor_summary_history_key(entity_id), trace_id
            )
            self._prune_trace_rows(
                self.corridor_regime_history_key(entity_id), trace_id
            )
            self._prune_trace_rows(
                self.corridor_incident_history_key(entity_id), trace_id
            )
            self.client.delete(self.corridor_meta_key(entity_id, trace_id=trace_id))
        self.client.delete(
            self.trace_vehicle_entities_key(trace_id),
            self.trace_corridor_entities_key(trace_id),
            self.trace_meta_key(trace_id),
            *[self.trace_payload_key(trace_id, kind) for kind in SNAPSHOT_PARTS],
        )
        self.client.srem("transit:trace_ids", trace_id)
        self.client.zrem("transit:trace_timestamps", trace_id)
        self._refresh_replay_latest_payloads()
        self.client.set("transit:sources:last", self._dumps(self.sources()))

    def latest_replay_trace_id(self) -> str | None:
        values = self.client.zrevrange("transit:trace_timestamps", 0, 0) or []
        if values:
            return str(values[0])
        trace_ids = self.list_trace_ids()
        return trace_ids[0] if trace_ids else None

    def _read_latest_snapshot_part(
        self,
        kind: str,
        *,
        scope: str,
        trace_id: str | None,
        default: Dict[str, Any],
    ) -> Dict[str, Any]:
        explicit_trace = trace_id not in (None, "")
        replay_enabled = self._replay_enabled()
        if not replay_enabled and (explicit_trace or scope == "replay"):
            return dict(default)
        resolved_trace_id = self._resolve_trace_id(scope=scope, trace_id=trace_id)
        keys: List[str] = []
        if resolved_trace_id:
            keys.append(self.trace_payload_key(resolved_trace_id, kind))
        if explicit_trace:
            pass
        elif replay_enabled and scope == "replay":
            keys.append(self.replay_payload_key(kind))
        else:
            keys.append(self.live_payload_key(kind))
            if replay_enabled and scope == "all":
                keys.append(self.replay_payload_key(kind))
        if replay_enabled:
            keys.append(f"transit:{kind}:last")
        for key in keys:
            payload = self.read_json_key(key, default={})
            if payload:
                return payload
        return dict(default)

    def _write_latest_snapshot_part(
        self,
        pipe,
        kind: str,
        payload: Dict[str, Any],
        *,
        source: str,
        trace_id: str | None,
        timestamp_ms: int,
    ) -> None:
        normalized_source = str(source or "live")
        if normalized_source == "replay" and trace_id:
            pipe.set(self.trace_payload_key(trace_id, kind), self._dumps(payload))
            pipe.set(self.replay_payload_key(kind), self._dumps(payload))
            pipe.sadd("transit:trace_ids", trace_id)
            pipe.zadd("transit:trace_timestamps", {trace_id: timestamp_ms})
            return
        pipe.set(self.live_payload_key(kind), self._dumps(payload))

    def _resolve_trace_id(self, *, scope: str, trace_id: str | None) -> str | None:
        if not self._replay_enabled():
            return None
        if trace_id not in (None, ""):
            return str(trace_id)
        if scope == "replay":
            return self.latest_replay_trace_id()
        return None

    def _refresh_replay_latest_payloads(self) -> None:
        latest_trace_id = self.latest_replay_trace_id()
        if not latest_trace_id:
            self.client.delete(
                *[self.replay_payload_key(kind) for kind in SNAPSHOT_PARTS]
            )
            return
        for kind in SNAPSHOT_PARTS:
            payload = self.read_json_key(
                self.trace_payload_key(latest_trace_id, kind), default={}
            )
            if payload:
                self.client.set(self.replay_payload_key(kind), self._dumps(payload))

    def _prune_trace_rows(self, key: str, trace_id: str) -> None:
        rows = self.client.zrange(key, 0, -1) or []
        to_remove = [
            row
            for row in rows
            if str((self._loads(row) or {}).get("trace_id") or "") == trace_id
        ]
        if to_remove:
            self.client.zrem(key, *to_remove)

    def _normalize_entities_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(payload)
        for key in ["lines", "active_lines", "scheduled_later_lines", "inactive_lines"]:
            normalized[key] = [
                TransitCorridorSnapshot.from_mapping(row).to_json()
                for row in (payload.get(key) or [])
                if isinstance(row, dict)
            ]
            normalized[key].sort(key=self._corridor_sort_key)
        normalized["vehicles"] = [
            TransitVehicleSnapshot.from_mapping(row).to_json()
            for row in (payload.get("vehicles") or [])
            if isinstance(row, dict)
        ]
        return normalized

    @staticmethod
    def _with_read_model_metadata(
        payload: Dict[str, Any],
        *,
        kind: str,
        generated_at: str,
        **metadata: Any,
    ) -> Dict[str, Any]:
        return {
            **copy.deepcopy(payload),
            "read_model": {
                "kind": kind,
                "scope": "live",
                "generated_at": generated_at,
                **metadata,
            },
        }

    @staticmethod
    def _corridor_sort_key(row: Dict[str, Any]) -> tuple[int, float, int, int, str]:
        return (
            -int(row.get("priority_score") or 0),
            -float(row.get("avg_hazard") or 0.0),
            -int(row.get("active_alert_count") or 0),
            -int(row.get("median_delay_seconds") or 0),
            str(row.get("label") or row.get("entity_id") or ""),
        )

    @staticmethod
    def _incident_sort_key(row: Dict[str, Any]) -> tuple[int, float, int, str]:
        return (
            -int(row.get("priority_score") or 0),
            -float(row.get("hazard") or 0.0),
            -int(row.get("timestamp_ms") or 0),
            str(row.get("label") or row.get("entity_id") or ""),
        )

    def _optional_sorted_set_score(self, key: str, member: str) -> int | None:
        raw = self.client.zscore(key, member)
        if raw is None:
            return None
        return int(raw)

    @staticmethod
    def _apply_snapshot_context(
        health: Dict[str, Any],
        entities: Dict[str, Any],
        regimes: Dict[str, Any],
        incidents: Dict[str, Any],
        *,
        source: str,
        trace_id: str | None,
        timestamp_ms: int,
    ) -> None:
        def _mark(row: Dict[str, Any]) -> None:
            row["source"] = source
            row["trace_id"] = trace_id
            if "timestamp_ms" not in row or not row.get("timestamp_ms"):
                row["timestamp_ms"] = timestamp_ms

        worst = health.get("worst_corridor")
        if isinstance(worst, dict):
            _mark(worst)

        for key in ["lines", "active_lines", "scheduled_later_lines", "inactive_lines"]:
            for row in entities.get(key) or []:
                if isinstance(row, dict):
                    _mark(row)

        for vehicle in entities.get("vehicles") or []:
            if not isinstance(vehicle, dict):
                continue
            _mark(vehicle)
            observation = vehicle.get("observation")
            if isinstance(observation, dict):
                _mark(observation)
            regime = vehicle.get("regime")
            if isinstance(regime, dict):
                _mark(regime)

        for row in regimes.get("regimes") or []:
            if isinstance(row, dict):
                _mark(row)

        for row in incidents.get("incidents") or []:
            if isinstance(row, dict):
                _mark(row)

    @staticmethod
    def _infer_snapshot_source(payload: Dict[str, Any]) -> str:
        for collection in [
            ((payload.get("entities") or {}).get("vehicles") or []),
            ((payload.get("entities") or {}).get("lines") or []),
            ((payload.get("regimes") or {}).get("regimes") or []),
            ((payload.get("incidents") or {}).get("incidents") or []),
        ]:
            for row in collection:
                if not isinstance(row, dict):
                    continue
                source = row.get("source")
                if isinstance(source, str) and source:
                    return source
                nested = row.get("observation")
                if isinstance(nested, dict) and nested.get("source"):
                    return str(nested["source"])
        return "live"

    @staticmethod
    def _infer_snapshot_trace_id(payload: Dict[str, Any]) -> str | None:
        for collection in [
            ((payload.get("entities") or {}).get("vehicles") or []),
            ((payload.get("entities") or {}).get("lines") or []),
            ((payload.get("regimes") or {}).get("regimes") or []),
            ((payload.get("incidents") or {}).get("incidents") or []),
        ]:
            for row in collection:
                if not isinstance(row, dict):
                    continue
                value = str(row.get("trace_id") or "").strip()
                if value:
                    return value
                nested = row.get("observation")
                if isinstance(nested, dict):
                    nested_value = str(nested.get("trace_id") or "").strip()
                    if nested_value:
                        return nested_value
        return None

    @staticmethod
    def _infer_snapshot_timestamp_ms(payload: Dict[str, Any]) -> int:
        candidates: List[int] = []
        for collection in [
            ((payload.get("entities") or {}).get("vehicles") or []),
            ((payload.get("entities") or {}).get("lines") or []),
            ((payload.get("regimes") or {}).get("regimes") or []),
            ((payload.get("incidents") or {}).get("incidents") or []),
        ]:
            for row in collection:
                if not isinstance(row, dict):
                    continue
                timestamp_ms = int(row.get("timestamp_ms") or 0)
                if timestamp_ms > 0:
                    candidates.append(timestamp_ms)
                nested = row.get("observation")
                if isinstance(nested, dict):
                    nested_timestamp_ms = int(nested.get("timestamp_ms") or 0)
                    if nested_timestamp_ms > 0:
                        candidates.append(nested_timestamp_ms)
        return max(candidates) if candidates else int(time.time() * 1000)

    def _trim_sorted_set(self, key: str, retention: int) -> None:
        retention = max(1, int(retention or 1))
        count = int(self.client.zcard(key) or 0)
        if count > retention:
            self.client.zremrangebyrank(key, 0, count - retention - 1)
        ttl_seconds = self._history_ttl_seconds(retention)
        if ttl_seconds > 0:
            self.client.expire(key, ttl_seconds)

    @staticmethod
    def _pipe_trim_sorted_set(
        pipe: Any, key: str, retention: int, *, ttl_seconds: int = 0
    ) -> None:
        retention = max(1, int(retention or 1))
        pipe.zremrangebyrank(key, 0, -retention - 1)
        if ttl_seconds > 0:
            pipe.expire(key, ttl_seconds)

    @staticmethod
    def _history_ttl_seconds(retention: int) -> int:
        raw = os.getenv("TRANSIT_HISTORY_TTL_SECONDS")
        if raw is not None and str(raw).strip() != "":
            try:
                return max(0, int(float(raw)))
            except ValueError:
                return 0
        interval_seconds = max(1.0, _float_env("TRANSIT_HISTORY_INTERVAL_SECONDS", 60.0))
        return max(60, int(math.ceil(max(1, int(retention or 1)) * interval_seconds)))

    @staticmethod
    def _replay_enabled() -> bool:
        return str(os.getenv("TRANSIT_REPLAY_ENABLED", "1")).strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }

    @staticmethod
    def _default_health() -> Dict[str, Any]:
        return {
            "generated_at": isoformat_ms(),
            "status": "idle",
            "line_count": 0,
            "active_line_count": 0,
            "scheduled_later_line_count": 0,
            "inactive_line_count": 0,
            "visible_line_count": 0,
            "vehicle_count": 0,
            "incident_count": 0,
            "critical_incidents": 0,
            "avg_hazard": 0.0,
            "avg_confidence": 0.0,
            "max_hazard": 0.0,
            "action_counts": {},
            "regime_counts": {},
            "feed_status": {
                "feed_label": None,
                "updated_at": None,
                "vehicle_count": 0,
                "trip_update_count": 0,
                "alert_count": 0,
                "collection_source": "gtfs_rt",
                "status": "idle",
            },
            "worst_corridor": None,
        }

    @staticmethod
    def _default_entities() -> Dict[str, Any]:
        return {
            "generated_at": isoformat_ms(),
            "lines": [],
            "active_lines": [],
            "scheduled_later_lines": [],
            "inactive_lines": [],
            "vehicles": [],
        }

    @staticmethod
    def _default_regimes() -> Dict[str, Any]:
        return {
            "generated_at": isoformat_ms(),
            "regimes": [],
            "recurring_regimes": [],
        }

    @staticmethod
    def _default_incidents() -> Dict[str, Any]:
        return {
            "generated_at": isoformat_ms(),
            "incidents": [],
        }

    @staticmethod
    def _dumps(payload: Dict[str, Any]) -> str:
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _loads(raw: Any) -> Dict[str, Any]:
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}


def _dashboard_entities_read_model(entities: Dict[str, Any]) -> Dict[str, Any]:
    """Return the compact entity shape consumed by the operations dashboard."""
    return {
        "generated_at": entities.get("generated_at"),
        "agency_key": entities.get("agency_key"),
        "lines": [
            _dashboard_corridor_read_model(row)
            for row in (entities.get("lines") or [])
            if isinstance(row, dict)
        ],
        "active_lines": [
            _dashboard_corridor_read_model(row)
            for row in (entities.get("active_lines") or [])
            if isinstance(row, dict)
        ],
        "scheduled_later_lines": [
            _dashboard_corridor_read_model(row)
            for row in (entities.get("scheduled_later_lines") or [])
            if isinstance(row, dict)
        ],
        "inactive_lines": [
            _dashboard_corridor_read_model(row)
            for row in (entities.get("inactive_lines") or [])
            if isinstance(row, dict)
        ],
        "vehicles": [
            _dashboard_vehicle_read_model(row)
            for row in (entities.get("vehicles") or [])
            if isinstance(row, dict)
        ],
    }


def _dashboard_corridor_read_model(row: Dict[str, Any]) -> Dict[str, Any]:
    keys = (
        "entity_id",
        "agency_key",
        "corridor_id",
        "route_id",
        "direction_id",
        "label",
        "vehicle_count",
        "median_delay_seconds",
        "scheduled_headway_seconds",
        "compressed_headway_share",
        "avg_delay_seconds",
        "top_action",
        "top_action_label",
        "avg_hazard",
        "active_alert_count",
        "current_regime",
        "current_regime_label",
        "priority_score",
        "priority_label",
        "activity_status",
        "activity_status_label",
        "activity_reason",
        "activity_reason_label",
        "route_mode",
        "source",
        "collection_source",
        "trace_id",
        "timestamp_ms",
    )
    return {key: row.get(key) for key in keys if key in row}


def _dashboard_vehicle_read_model(row: Dict[str, Any]) -> Dict[str, Any]:
    keys = (
        "entity_id",
        "label",
        "vehicle_id",
        "corridor_entity_id",
        "agency_key",
        "corridor_id",
        "route_id",
        "route_label",
        "trip_id",
        "direction_id",
        "stop_id",
        "status",
        "delay_seconds",
        "occupancy_status",
        "source",
        "collection_source",
    )
    payload = {key: row.get(key) for key in keys if key in row}
    regime = row.get("regime")
    if isinstance(regime, dict):
        payload["regime"] = {
            key: regime.get(key)
            for key in (
                "entity_id",
                "label",
                "route_id",
                "regime",
                "regime_label",
                "hazard",
                "action",
                "action_label",
                "confidence",
                "priority_score",
                "priority_label",
                "timestamp_ms",
            )
            if key in regime
        }
    return payload


def _public_status_network_read_model(
    health: Dict[str, Any],
    entities: Dict[str, Any],
    regimes_payload: Dict[str, Any],
    incidents_payload: Dict[str, Any],
    *,
    generated_at: str,
) -> Dict[str, Any]:
    routes = _public_route_status_rows(entities, regimes_payload, incidents_payload)
    route_severities = [str(row.get("severity") or "good") for row in routes]
    network_severity = classify_network_severity(route_severities)
    active_count = int(health.get("active_line_count") or health.get("line_count") or 0)
    incident_count = int(health.get("incident_count") or 0)
    critical_count = int(health.get("critical_incidents") or 0)
    disrupted_routes = [
        {
            "entity_id": row.get("entity_id"),
            "label": row.get("label"),
            "severity": row.get("severity"),
        }
        for row in routes
        if row.get("severity") in ("delay", "disruption", "severe")
    ]
    return {
        "generated_at": generated_at,
        "scope": "live",
        "severity": network_severity,
        "severity_label": SEVERITY_LABELS.get(network_severity, network_severity),
        "severity_color": SEVERITY_COLOR.get(network_severity, "gray"),
        "active_route_count": active_count,
        "incident_count": incident_count,
        "critical_incident_count": critical_count,
        "disrupted_route_count": len(disrupted_routes),
        "disrupted_routes": disrupted_routes,
        "feed_status": health.get("feed_status"),
    }


def _public_route_status_rows(
    entities: Dict[str, Any],
    regimes_payload: Dict[str, Any],
    incidents_payload: Dict[str, Any],
) -> List[Dict[str, Any]]:
    regime_by_entity: Dict[str, Any] = {
        str(row.get("entity_id") or ""): row
        for row in (regimes_payload.get("regimes") or [])
        if isinstance(row, dict) and row.get("entity_id")
    }
    incidents_by_entity: Dict[str, list] = {}
    for incident in incidents_payload.get("incidents") or []:
        if not isinstance(incident, dict):
            continue
        entity_id = str(incident.get("entity_id") or "")
        incidents_by_entity.setdefault(entity_id, []).append(incident)

    seen: set[str] = set()
    rows: List[Dict[str, Any]] = []
    for line in (entities.get("active_lines") or []) + (
        entities.get("scheduled_later_lines") or []
    ):
        if not isinstance(line, dict):
            continue
        entity_id = str(line.get("entity_id") or "")
        if not entity_id or entity_id in seen:
            continue
        seen.add(entity_id)
        rows.append(
            build_route_status(line, regime_by_entity, incidents_by_entity).to_json()
        )

    rows.sort(
        key=lambda row: (
            -severity_rank(str(row.get("severity") or "good")),
            str(row.get("label") or ""),
        )
    )
    return rows


def _snapshot_parts_are_live(snapshot_parts: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(snapshot_parts, dict) or not snapshot_parts:
        return False
    if str(snapshot_parts.get("source") or "live") != "live":
        return False
    trace_id = snapshot_parts.get("trace_id")
    if trace_id not in (None, ""):
        return False
    return all(
        isinstance(snapshot_parts.get(name), dict)
        for name in ("health", "entities", "regimes", "incidents")
    )


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default
