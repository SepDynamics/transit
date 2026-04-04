#!/usr/bin/env python3
"""HTTP API for the legacy Cluster Sentinel dashboard."""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cluster.policy_engine import active_regimes, build_cluster_health, evaluate_incidents
from scripts.cluster.storage import ClusterStore, scope_matches

logger = logging.getLogger("cluster-api")


class ClusterAPIService:
    def __init__(
        self,
        redis_url: str,
        *,
        cluster_name: str = "Cluster Sentinel Demo",
        stale_after_seconds: Optional[int] = None,
    ) -> None:
        self.cluster_name = cluster_name
        self.store = ClusterStore(redis_url)
        self.stale_after_seconds = max(1, int(stale_after_seconds or os.getenv("POLICY_STALE_AFTER_SECONDS", "30")))

    def service_health(self) -> Dict[str, Any]:
        collector_status = self.store.read_status("ops:collector_status")
        regime_status = self.store.read_status("ops:regime_status")
        policy_status = self.store.read_status("ops:policy_status")
        return {
            "service": "Cluster Sentinel API",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cluster_name": self.cluster_name,
            "collector_status": collector_status,
            "regime_status": regime_status,
            "policy_status": policy_status,
            "status": "ok",
        }

    def cluster_health(self, *, scope: str = "all", trace_id: str | None = None) -> Dict[str, Any]:
        regimes = active_regimes(self.store, stale_after_seconds=self.stale_after_seconds, scope=scope, trace_id=trace_id)
        incidents = evaluate_incidents(regimes, stale_after_seconds=self.stale_after_seconds)
        collector_status = self.store.read_status("ops:collector_status")
        health = build_cluster_health(
            cluster_name=self.cluster_name,
            regimes=regimes,
            incidents=incidents,
            collector_status=collector_status,
        )
        health["scope"] = scope
        health["trace_id"] = trace_id
        return health

    def gpu_inventory(self, *, scope: str = "all", trace_id: str | None = None) -> Dict[str, Any]:
        entities = self.store.list_entities(scope=scope, stale_after_seconds=self.stale_after_seconds, trace_id=trace_id)
        gpus: List[Dict[str, Any]] = []
        nodes: Dict[str, Dict[str, Any]] = {}
        for entity in entities:
            sample = entity.get("sample") or {}
            regime = entity.get("regime") or {}
            host = str(entity["host"])
            node = nodes.setdefault(
                host,
                {
                    "host": host,
                    "gpu_count": 0,
                    "avg_gpu_util": 0.0,
                    "avg_hazard": 0.0,
                    "max_temperature_c": 0.0,
                    "top_action": "watch",
                    "source_breakdown": {},
                },
            )
            node["gpu_count"] += 1
            node["avg_gpu_util"] += float(sample.get("gpu_util") or 0.0)
            node["avg_hazard"] += float(regime.get("hazard") or 0.0)
            node["max_temperature_c"] = max(
                float(node["max_temperature_c"] or 0.0),
                float(sample.get("temperature_c") or 0.0),
            )
            action = str(regime.get("action") or "watch")
            if action_priority(action) > action_priority(str(node["top_action"])):
                node["top_action"] = action
            source = str(sample.get("source") or "live")
            node["source_breakdown"][source] = node["source_breakdown"].get(source, 0) + 1
            gpus.append(
                {
                    "entity_id": f"{host}:{entity['gpu_index']}",
                    "host": host,
                    "gpu_index": entity["gpu_index"],
                    "uuid": sample.get("uuid"),
                    "name": sample.get("name"),
                    "source": sample.get("source"),
                    "collection_source": sample.get("collection_source"),
                    "trace_id": sample.get("trace_id"),
                    "last_seen_ms": entity.get("last_seen_ms"),
                    "telemetry": sample,
                    "regime": regime,
                }
            )
        for node in nodes.values():
            if node["gpu_count"] > 0:
                node["avg_gpu_util"] = round(node["avg_gpu_util"] / node["gpu_count"], 2)
                node["avg_hazard"] = round(node["avg_hazard"] / node["gpu_count"], 4)
        node_list = sorted(nodes.values(), key=lambda item: (-float(item["avg_hazard"]), item["host"]))
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scope": scope,
            "trace_id": trace_id,
            "nodes": node_list,
            "gpus": gpus,
        }

    def latest_regimes(self, *, scope: str = "all", trace_id: str | None = None) -> Dict[str, Any]:
        regimes = active_regimes(self.store, stale_after_seconds=self.stale_after_seconds, scope=scope, trace_id=trace_id)
        signatures: Dict[str, Dict[str, Any]] = {}
        for regime in regimes:
            signature = str(regime.get("signature") or "")
            if not signature:
                continue
            bucket = signatures.setdefault(
                signature,
                {
                    "signature": signature,
                    "entity_count": 0,
                    "repetitions": 0,
                    "hazard_max": 0.0,
                    "regimes": set(),
                    "actions": set(),
                    "entities": [],
                },
            )
            bucket["entity_count"] += 1
            bucket["repetitions"] = max(bucket["repetitions"], int(regime.get("repetitions") or 0))
            bucket["hazard_max"] = max(bucket["hazard_max"], float(regime.get("hazard") or 0.0))
            bucket["regimes"].add(str(regime.get("regime") or "unknown"))
            bucket["actions"].add(str(regime.get("action") or "watch"))
            bucket["entities"].append(f"{regime.get('host')}:gpu{regime.get('gpu_index')}")
        recurring = [
            {
                **payload,
                "regimes": sorted(payload["regimes"]),
                "actions": sorted(payload["actions"]),
            }
            for payload in signatures.values()
            if payload["entity_count"] > 1 or payload["repetitions"] > 1
        ]
        recurring.sort(key=lambda item: (-int(item["repetitions"]), -float(item["hazard_max"])))
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scope": scope,
            "trace_id": trace_id,
            "regimes": regimes,
            "recurring_signatures": recurring[:10],
        }

    def incidents(self, *, scope: str = "all", trace_id: str | None = None) -> Dict[str, Any]:
        regimes = active_regimes(self.store, stale_after_seconds=self.stale_after_seconds, scope=scope, trace_id=trace_id)
        incidents = [incident.to_json() for incident in evaluate_incidents(regimes, stale_after_seconds=self.stale_after_seconds)]
        incidents.sort(key=lambda item: (-action_priority(str(item.get("action") or "watch")), -float(item.get("hazard") or 0.0)))
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scope": scope,
            "trace_id": trace_id,
            "incidents": incidents,
        }

    def history(
        self,
        *,
        host: str,
        gpu_index: int,
        scope: str = "all",
        trace_id: str | None = None,
        limit: int = 120,
    ) -> Dict[str, Any]:
        telemetry = [
            row
            for row in self.store.get_recent_samples(host, gpu_index, limit=limit)
            if scope_matches(row, scope) and (trace_id in (None, "") or str(row.get("trace_id") or "") == trace_id)
        ]
        regimes = [
            row
            for row in self.store.get_recent_regimes(host, gpu_index, limit=limit)
            if scope_matches(row, scope) and (trace_id in (None, "") or str(row.get("trace_id") or "") == trace_id)
        ]
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scope": scope,
            "trace_id": trace_id,
            "entity": {"host": host, "gpu_index": gpu_index},
            "telemetry": telemetry,
            "regimes": regimes,
        }

    def sources(self) -> Dict[str, Any]:
        entities = self.store.list_entities(scope="all", stale_after_seconds=self.stale_after_seconds)
        trace_ids = self.store.list_trace_ids()
        has_replay = any((entity.get("sample") or {}).get("source") == "replay" for entity in entities)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scopes": [
                {"id": "all", "label": "All streams"},
                {"id": "live", "label": "Live fleet"},
                {"id": "replay", "label": "Replay"},
            ],
            "available": {
                "live": any((entity.get("sample") or {}).get("source") == "live" for entity in entities),
                "replay": has_replay,
            },
            "trace_ids": trace_ids,
        }

class ClusterAPIHandler(BaseHTTPRequestHandler):
    server_version = "ClusterSentinel/1.0"

    @property
    def svc(self) -> ClusterAPIService:  # type: ignore[override]
        return getattr(self.server, "cluster_service")

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # pragma: no cover - exercised via integration test
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query or "")
        scope = (params.get("scope") or ["all"])[0]
        trace_id = (params.get("trace_id") or [""])[0] or None
        if parsed.path in {"/health", "/api/health", "/api/status"}:
            self._send_json(self.svc.service_health())
            return
        if parsed.path == "/api/cluster/health":
            self._send_json(self.svc.cluster_health(scope=scope, trace_id=trace_id))
            return
        if parsed.path == "/api/cluster/gpus":
            self._send_json(self.svc.gpu_inventory(scope=scope, trace_id=trace_id))
            return
        if parsed.path == "/api/cluster/regimes":
            self._send_json(self.svc.latest_regimes(scope=scope, trace_id=trace_id))
            return
        if parsed.path == "/api/cluster/incidents":
            self._send_json(self.svc.incidents(scope=scope, trace_id=trace_id))
            return
        if parsed.path == "/api/cluster/history":
            host = (params.get("host") or [""])[0]
            gpu_index_raw = (params.get("gpu_index") or ["0"])[0]
            limit_raw = (params.get("limit") or ["120"])[0]
            try:
                gpu_index = int(gpu_index_raw)
            except ValueError:
                gpu_index = 0
            try:
                limit = int(limit_raw)
            except ValueError:
                limit = 120
            self._send_json(
                self.svc.history(
                    host=host,
                    gpu_index=gpu_index,
                    scope=scope,
                    trace_id=trace_id,
                    limit=max(10, min(limit, 1000)),
                )
            )
            return
        if parsed.path == "/api/cluster/sources":
            self._send_json(self.svc.sources())
            return
        self._send_json({"error": "not_found"}, status=404)

    def do_POST(self) -> None:  # pragma: no cover - no mutating endpoints yet
        self._send_json({"error": "not_found"}, status=404)

    def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover
        logger.info("%s - %s", self.address_string(), format % args)


def action_priority(action: str) -> int:
    return {"quarantine": 5, "drain": 4, "throttle": 3, "alert": 2, "watch": 1}.get(action, 0)


def start_http_server(service: ClusterAPIService, host: str = "0.0.0.0", port: int = 8000) -> HTTPServer:
    class _Server(HTTPServer):  # pragma: no cover
        def __init__(self, address):
            super().__init__(address, ClusterAPIHandler)
            self.cluster_service = service

    server = _Server((host, port))

    def _serve() -> None:
        logger.info("Cluster API listening on %s:%s", host, port)
        server.serve_forever()

    thread = threading.Thread(target=_serve, name="ClusterAPI", daemon=True)
    thread.start()
    return server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Cluster Sentinel API server")
    parser.add_argument("--redis", default=os.getenv("VALKEY_URL", "redis://localhost:6379/0"))
    parser.add_argument("--host", default=os.getenv("CLUSTER_API_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("CLUSTER_API_PORT", "8000")))
    parser.add_argument("--cluster-name", default=os.getenv("CLUSTER_NAME", "Cluster Sentinel Demo"))
    parser.add_argument("--stale-after-seconds", type=int, default=int(os.getenv("POLICY_STALE_AFTER_SECONDS", "30")))
    return parser


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
    args = build_parser().parse_args()
    service = ClusterAPIService(
        args.redis,
        cluster_name=str(args.cluster_name or "Cluster Sentinel Demo"),
        stale_after_seconds=max(1, int(args.stale_after_seconds)),
    )
    server = start_http_server(service, host=args.host, port=int(args.port))

    stop = False

    def _handle_signal(signum: int, _frame: Any) -> None:
        nonlocal stop
        logger.info("received signal %s, stopping api", signum)
        stop = True
        server.shutdown()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    while not stop:
        time.sleep(0.5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
