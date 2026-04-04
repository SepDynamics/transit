"""Valkey storage wrapper for Cluster Sentinel."""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

try:
    import redis  # type: ignore
except ImportError:  # pragma: no cover
    redis = None

from .models import TelemetrySample
from scripts.shared.runtime import scope_matches


class ClusterStore:
    """Thin JSON-oriented storage adapter built on top of Valkey/Redis."""

    def __init__(self, redis_url: Optional[str] = None, client: Any = None) -> None:
        if client is not None:
            self.client = client
            return
        if redis is None:
            raise RuntimeError("redis dependency is not installed")
        url = redis_url or os.getenv("VALKEY_URL") or os.getenv("REDIS_URL")
        if not url:
            raise RuntimeError("VALKEY_URL or REDIS_URL must be configured")
        self.client = redis.from_url(url, decode_responses=True)

    def record_sample(self, sample: TelemetrySample, *, retention: int = 720) -> None:
        payload = self._dumps(sample.to_json())
        key = self.telemetry_key(sample.host, sample.gpu_index)
        token = sample.entity_token()
        previous_meta = self.read_json_key(self.entity_meta_key(sample.host, sample.gpu_index), default={})
        self.client.zadd(key, {payload: sample.timestamp_ms})
        self._trim_sorted_set(key, retention)
        self.client.set(self.telemetry_meta_key(sample.host, sample.gpu_index), payload)
        self.client.set(
            self.entity_meta_key(sample.host, sample.gpu_index),
            self._dumps(
                {
                    "host": sample.host,
                    "gpu_index": sample.gpu_index,
                    "last_seen_ms": sample.timestamp_ms,
                    "source": sample.source,
                    "collection_source": sample.collection_source,
                    "trace_id": sample.trace_id,
                }
            ),
        )
        self.client.sadd("telemetry:entities", token)
        self.client.sadd(self.source_entities_key(sample.source), token)
        if previous_meta.get("source") and previous_meta.get("source") != sample.source:
            self.client.srem(self.source_entities_key(str(previous_meta["source"])), token)
        previous_trace = previous_meta.get("trace_id")
        if previous_trace and previous_trace != sample.trace_id:
            self.client.srem(self.trace_entities_key(str(previous_trace)), token)
            self._cleanup_trace_index(str(previous_trace))
        if sample.trace_id:
            self.client.sadd(self.trace_entities_key(sample.trace_id), token)
            self.client.sadd("telemetry:trace_ids", sample.trace_id)

    def write_regime(self, payload: Dict[str, Any], *, retention: int = 720) -> None:
        host = str(payload.get("host") or "unknown-host")
        gpu_index = int(payload.get("gpu_index") or 0)
        body = self._dumps(payload)
        key = self.regime_history_key(host, gpu_index)
        score = int(payload.get("timestamp_ms") or 0)
        self.client.set(self.regime_last_key(host, gpu_index), body)
        self.client.zadd(key, {body: score})
        self._trim_sorted_set(key, retention)

    def write_cluster_health(self, payload: Dict[str, Any]) -> None:
        self.client.set("ops:cluster_health", self._dumps(payload))

    def write_incident_summary(self, payload: Dict[str, Any]) -> None:
        self.client.set("ops:incident_summary", self._dumps(payload))

    def write_status(self, key: str, payload: Dict[str, Any]) -> None:
        self.client.set(key, self._dumps(payload))

    def read_cluster_health(self) -> Dict[str, Any]:
        return self.read_json_key("ops:cluster_health", default={})

    def read_incident_summary(self) -> Dict[str, Any]:
        return self.read_json_key("ops:incident_summary", default={"incidents": []})

    def read_status(self, key: str) -> Dict[str, Any]:
        return self.read_json_key(key, default={})

    def read_json_key(self, key: str, *, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        raw = self.client.get(key)
        if not raw:
            return dict(default or {})
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return dict(default or {})

    def list_entities(
        self,
        *,
        scope: str = "all",
        stale_after_seconds: Optional[int] = None,
        now_ms: Optional[int] = None,
        trace_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        entities: List[Dict[str, Any]] = []
        cutoff_ms = None
        if stale_after_seconds is not None:
            cutoff_ms = int((now_ms if now_ms is not None else int(time.time() * 1000)) - (max(0, stale_after_seconds) * 1000))
        members = sorted(self.client.smembers("telemetry:entities") or [])
        for member in members:
            try:
                host, gpu_raw = member.split("|", 1)
                gpu_index = int(gpu_raw)
            except (ValueError, TypeError):
                continue
            sample = self.get_latest_sample(host, gpu_index)
            if not sample or not scope_matches(sample, scope):
                continue
            sample_trace_id = sample.get("trace_id")
            if trace_id not in (None, "") and sample_trace_id != trace_id:
                continue
            if cutoff_ms is not None and int(sample.get("timestamp_ms") or 0) < cutoff_ms:
                continue
            regime = self.get_latest_regime(host, gpu_index)
            meta = self.get_entity_meta(host, gpu_index)
            entities.append(
                {
                    "host": host,
                    "gpu_index": gpu_index,
                    "sample": sample,
                    "regime": regime,
                    "meta": meta,
                    "last_seen_ms": int(meta.get("last_seen_ms") or sample.get("timestamp_ms") or 0),
                }
            )
        entities.sort(
            key=lambda item: (
                -float((item.get("regime") or {}).get("hazard") or 0.0),
                item["host"],
                item["gpu_index"],
            )
        )
        return entities

    def get_latest_sample(self, host: str, gpu_index: int) -> Dict[str, Any]:
        raw = self.client.get(self.telemetry_meta_key(host, gpu_index))
        if not raw:
            rows = self.client.zrange(self.telemetry_key(host, gpu_index), -1, -1)
            raw = rows[0] if rows else None
        return self._loads(raw)

    def get_recent_samples(self, host: str, gpu_index: int, *, limit: int = 120) -> List[Dict[str, Any]]:
        rows = self.client.zrange(self.telemetry_key(host, gpu_index), -limit, -1) or []
        return [payload for payload in (self._loads(row) for row in rows) if payload]

    def get_entity_meta(self, host: str, gpu_index: int) -> Dict[str, Any]:
        return self.read_json_key(self.entity_meta_key(host, gpu_index), default={})

    def get_latest_regime(self, host: str, gpu_index: int) -> Dict[str, Any]:
        return self.read_json_key(self.regime_last_key(host, gpu_index), default={})

    def get_recent_regimes(self, host: str, gpu_index: int, *, limit: int = 120) -> List[Dict[str, Any]]:
        rows = self.client.zrange(self.regime_history_key(host, gpu_index), -limit, -1) or []
        return [payload for payload in (self._loads(row) for row in rows) if payload]

    def clear_entity(self, host: str, gpu_index: int) -> bool:
        sample = self.get_latest_sample(host, gpu_index)
        meta = self.get_entity_meta(host, gpu_index)
        token = f"{host}|{gpu_index}"
        source = str(meta.get("source") or sample.get("source") or "")
        trace_id = meta.get("trace_id") if meta.get("trace_id") not in (None, "") else sample.get("trace_id")
        self.client.delete(
            self.telemetry_key(host, gpu_index),
            self.telemetry_meta_key(host, gpu_index),
            self.entity_meta_key(host, gpu_index),
            self.regime_last_key(host, gpu_index),
            self.regime_history_key(host, gpu_index),
        )
        self.client.srem("telemetry:entities", token)
        if source:
            self.client.srem(self.source_entities_key(source), token)
        if trace_id:
            self.client.srem(self.trace_entities_key(str(trace_id)), token)
            self._cleanup_trace_index(str(trace_id))
        return bool(sample or meta)

    def clear_replay_trace(self, trace_id: str) -> int:
        members = sorted(self.client.smembers(self.trace_entities_key(trace_id)) or [])
        cleared = 0
        for member in members:
            try:
                host, gpu_raw = member.split("|", 1)
                gpu_index = int(gpu_raw)
            except (ValueError, TypeError):
                continue
            if self.clear_entity(host, gpu_index):
                cleared += 1
        self.client.delete(self.trace_entities_key(trace_id))
        self.client.srem("telemetry:trace_ids", trace_id)
        return cleared

    def purge_expired_entities(
        self,
        *,
        stale_after_seconds: int,
        scope: str = "all",
        now_ms: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        effective_now = int(now_ms if now_ms is not None else int(time.time() * 1000))
        cutoff_ms = effective_now - (max(0, stale_after_seconds) * 1000)
        removed: List[Dict[str, Any]] = []
        for entity in self.list_entities(scope=scope):
            last_seen_ms = int(entity.get("last_seen_ms") or 0)
            if last_seen_ms >= cutoff_ms:
                continue
            if self.clear_entity(str(entity["host"]), int(entity["gpu_index"])):
                removed.append(
                    {
                        "host": str(entity["host"]),
                        "gpu_index": int(entity["gpu_index"]),
                        "source": str((entity.get("sample") or {}).get("source") or "live"),
                        "trace_id": (entity.get("sample") or {}).get("trace_id"),
                        "last_seen_ms": last_seen_ms,
                    }
                )
        return removed

    def list_trace_ids(self) -> List[str]:
        return sorted(str(value) for value in (self.client.smembers("telemetry:trace_ids") or set()) if value)

    @staticmethod
    def telemetry_key(host: str, gpu_index: int) -> str:
        return f"telemetry:gpu:{host}:{gpu_index}"

    @staticmethod
    def telemetry_meta_key(host: str, gpu_index: int) -> str:
        return f"telemetry:meta:{host}:{gpu_index}"

    @staticmethod
    def entity_meta_key(host: str, gpu_index: int) -> str:
        return f"telemetry:entity:{host}:{gpu_index}"

    @staticmethod
    def source_entities_key(source: str) -> str:
        return f"telemetry:source:{source}:entities"

    @staticmethod
    def trace_entities_key(trace_id: str) -> str:
        return f"telemetry:trace:{trace_id}:entities"

    @staticmethod
    def regime_last_key(host: str, gpu_index: int) -> str:
        return f"regime:last:{host}:{gpu_index}"

    @staticmethod
    def regime_history_key(host: str, gpu_index: int) -> str:
        return f"regime:history:{host}:{gpu_index}"

    def _trim_sorted_set(self, key: str, retention: int) -> None:
        retention = max(1, int(retention or 1))
        count = int(self.client.zcard(key) or 0)
        if count > retention:
            self.client.zremrangebyrank(key, 0, count - retention - 1)

    def _cleanup_trace_index(self, trace_id: str) -> None:
        if int(self.client.scard(self.trace_entities_key(trace_id)) or 0) == 0:
            self.client.delete(self.trace_entities_key(trace_id))
            self.client.srem("telemetry:trace_ids", trace_id)

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
