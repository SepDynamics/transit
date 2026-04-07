from scripts.transit.store import TransitStore


def test_transit_store_persists_latest_payloads_and_vehicle_history(valkey_url):
    store = TransitStore(valkey_url)

    store.write_snapshot(_snapshot(timestamp_ms=1_710_000_100_000, delay_seconds=90, hazard=0.42))
    store.write_snapshot(_snapshot(timestamp_ms=1_710_000_160_000, delay_seconds=240, hazard=0.81))

    health = store.health()
    entities = store.entities()
    regimes = store.regimes()
    incidents = store.incidents()
    history = store.history("vehicle:1811", limit=10)
    corridor_history = store.history("route:Red:0", limit=10)
    trends = store.trends(limit=5, window=10)

    assert health["line_count"] == 1
    assert health["vehicle_count"] == 1
    assert health["max_hazard"] == 0.81
    assert entities["vehicles"][0]["delay_seconds"] == 240
    assert entities["vehicles"][0]["corridor_entity_id"] == "route:Red:0"
    assert regimes["regimes"][0]["entity_id"] == "route:Red:0"
    assert incidents["incidents"][0]["entity_id"] == "route:Red:0"
    assert [row["delay_seconds"] for row in history["observations"]] == [90, 240]
    assert [row["hazard"] for row in history["regimes"]] == [0.42, 0.81]
    assert [row["median_delay_seconds"] for row in corridor_history["observations"]] == [90, 240]
    assert corridor_history["incidents"][0]["entity_id"] == "route:Red:0"
    assert trends["summary"]["corridor_count"] == 1
    assert trends["summary"]["recent_incident_count"] == 2
    assert trends["corridors"][0]["hazard_series"] == [0.42, 0.81]
    assert trends["corridors"][0]["latest_action"] == "dispatch_relief"


def test_transit_store_keeps_live_and_replay_snapshots_side_by_side(valkey_url):
    store = TransitStore(valkey_url)

    store.write_snapshot(_snapshot(timestamp_ms=1_710_000_100_000, delay_seconds=90, hazard=0.42))
    store.write_snapshot(
        _snapshot(timestamp_ms=1_710_000_200_000, delay_seconds=300, hazard=0.88, source="replay", trace_id="case-red"),
        source="replay",
        trace_id="case-red",
    )

    live_entities = store.entities(scope="live")
    replay_entities = store.entities(scope="replay", trace_id="case-red")
    replay_history = store.history("vehicle:1811", scope="replay", trace_id="case-red", limit=10)
    replay_trends = store.trends(scope="replay", trace_id="case-red", limit=5, window=10)
    sources = store.sources()

    assert live_entities["vehicles"][0]["source"] == "live"
    assert replay_entities["vehicles"][0]["source"] == "replay"
    assert replay_entities["trace_id"] == "case-red"
    assert replay_entities["vehicles"][0]["observation"]["trace_id"] == "case-red"
    assert [row["delay_seconds"] for row in replay_history["observations"]] == [300]
    assert replay_trends["corridors"][0]["latest_hazard"] == 0.88
    assert sources["available"]["live"] is True
    assert sources["available"]["replay"] is True
    assert {row["id"] for row in sources["scopes"]} == {"all", "live", "replay"}
    assert sources["trace_ids"] == ["case-red"]
    assert sources["traces"][0]["trace_id"] == "case-red"
    assert sources["traces"][0]["latest_snapshot_timestamp_ms"] == 1_710_000_200_000


def test_transit_store_clear_runtime_state_removes_live_replay_and_status_keys(valkey_url):
    store = TransitStore(valkey_url)

    store.write_snapshot(_snapshot(timestamp_ms=1_710_000_100_000, delay_seconds=90, hazard=0.42))
    store.write_snapshot(
        _snapshot(timestamp_ms=1_710_000_200_000, delay_seconds=300, hazard=0.88, source="replay", trace_id="case-red"),
        source="replay",
        trace_id="case-red",
    )
    store.write_status("ops:transit_demo_seed_status", {"status": "ok"})

    deleted = store.clear_runtime_state()

    assert deleted > 0
    assert store.health()["status"] == "idle"
    assert store.entities()["vehicles"] == []
    assert store.sources()["available"] == {"live": False, "replay": False}
    assert store.sources()["trace_ids"] == []
    assert store.read_status("ops:transit_demo_seed_status") == {}


def test_transit_store_scorecard_aggregates_network_kpis(valkey_url):
    store = TransitStore(valkey_url)

    store.write_snapshot(_snapshot(timestamp_ms=1_710_000_100_000, delay_seconds=90, hazard=0.42))
    store.write_snapshot(_snapshot(timestamp_ms=1_710_000_160_000, delay_seconds=240, hazard=0.81))

    scorecard = store.scorecard(limit=10)

    assert scorecard["window_snapshots"] == 2
    assert scorecard["corridor_count"] == 1
    assert scorecard["total_incidents"] == 2
    assert scorecard["network"]["avg_hazard"] == 0.615
    assert scorecard["network"]["avg_delay_seconds"] == 165
    assert scorecard["network"]["on_time_pct"] == 50.0
    assert scorecard["network"]["unstable_corridor_count"] == 1
    assert scorecard["corridors"][0]["entity_id"] == "route:Red:0"
    assert scorecard["corridors"][0]["avg_hazard"] == 0.615
    assert scorecard["corridors"][0]["hazard_p90"] == 0.81
    assert scorecard["corridors"][0]["on_time_pct"] == 50.0
    assert scorecard["corridors"][0]["top_action"] == "hold"


def test_transit_store_sorts_active_lines_and_incidents_by_priority(valkey_url):
    store = TransitStore(valkey_url)

    store.write_snapshot(
        {
            "errors": [],
            "feed_status": {
                "feed_label": "MBTA",
                "updated_at": "2024-03-09T00:00:00+00:00",
                "vehicle_count": 0,
                "trip_update_count": 2,
                "alert_count": 1,
                "collection_source": "gtfs_rt",
                "status": "ok",
            },
            "health": {
                "system_name": "MBTA",
                "generated_at": "2024-03-09T00:00:00+00:00",
                "status": "warning",
                "line_count": 2,
                "active_line_count": 2,
                "scheduled_later_line_count": 0,
                "inactive_line_count": 0,
                "visible_line_count": 2,
                "vehicle_count": 0,
                "incident_count": 2,
                "critical_incidents": 1,
                "avg_hazard": 0.6,
                "avg_confidence": 0.8,
                "max_hazard": 0.88,
                "action_counts": {"hold": 1, "dispatch_relief": 1},
                "regime_counts": {"bunching_onset": 1, "headway_collapse": 1},
                "feed_status": {
                    "feed_label": "MBTA",
                    "updated_at": "2024-03-09T00:00:00+00:00",
                    "vehicle_count": 0,
                    "trip_update_count": 2,
                    "alert_count": 1,
                    "collection_source": "gtfs_rt",
                    "status": "ok",
                },
                "worst_corridor": {
                    "timestamp_ms": 1_710_000_200_000,
                    "entity_id": "route:High:0",
                    "entity_type": "corridor",
                    "label": "High Priority Line",
                    "route_id": "High",
                    "regime": "headway_collapse",
                    "regime_label": "Severe bunching / service gap",
                    "hazard": 0.88,
                    "action": "dispatch_relief",
                    "action_label": "Dispatch relief",
                    "priority_score": 92,
                    "priority_label": "Immediate",
                    "scoring_backend": "heuristic_v1",
                    "confidence": 0.84,
                    "signature": "sig-high",
                    "reasons": ["headway_collapse"],
                    "provenance": {},
                    "metrics": {},
                },
            },
            "entities": {
                "generated_at": "2024-03-09T00:00:00+00:00",
                "lines": [
                    {
                        "entity_id": "route:Low:0",
                        "route_id": "Low",
                        "direction_id": 0,
                        "label": "Low Priority Line",
                        "vehicle_count": 0,
                        "median_delay_seconds": 180,
                        "avg_delay_seconds": 180.0,
                        "top_action": "hold",
                        "top_action_label": "Hold to rebalance",
                        "avg_hazard": 0.51,
                        "active_alert_count": 0,
                        "current_regime": "bunching_onset",
                        "current_regime_label": "Early bunching",
                        "priority_score": 61,
                        "priority_label": "Watch",
                        "activity_status": "active_now",
                        "activity_status_label": "Active now",
                        "activity_reason": "live_telemetry",
                        "activity_reason_label": "Live telemetry present",
                    },
                    {
                        "entity_id": "route:High:0",
                        "route_id": "High",
                        "direction_id": 0,
                        "label": "High Priority Line",
                        "vehicle_count": 0,
                        "median_delay_seconds": 480,
                        "avg_delay_seconds": 480.0,
                        "top_action": "dispatch_relief",
                        "top_action_label": "Dispatch relief",
                        "avg_hazard": 0.88,
                        "active_alert_count": 1,
                        "current_regime": "headway_collapse",
                        "current_regime_label": "Severe bunching / service gap",
                        "priority_score": 92,
                        "priority_label": "Immediate",
                        "activity_status": "active_now",
                        "activity_status_label": "Active now",
                        "activity_reason": "live_telemetry",
                        "activity_reason_label": "Live telemetry present",
                    },
                ],
                "active_lines": [
                    {
                        "entity_id": "route:Low:0",
                        "route_id": "Low",
                        "direction_id": 0,
                        "label": "Low Priority Line",
                        "vehicle_count": 0,
                        "median_delay_seconds": 180,
                        "avg_delay_seconds": 180.0,
                        "top_action": "hold",
                        "top_action_label": "Hold to rebalance",
                        "avg_hazard": 0.51,
                        "active_alert_count": 0,
                        "current_regime": "bunching_onset",
                        "current_regime_label": "Early bunching",
                        "priority_score": 61,
                        "priority_label": "Watch",
                        "activity_status": "active_now",
                        "activity_status_label": "Active now",
                        "activity_reason": "live_telemetry",
                        "activity_reason_label": "Live telemetry present",
                    },
                    {
                        "entity_id": "route:High:0",
                        "route_id": "High",
                        "direction_id": 0,
                        "label": "High Priority Line",
                        "vehicle_count": 0,
                        "median_delay_seconds": 480,
                        "avg_delay_seconds": 480.0,
                        "top_action": "dispatch_relief",
                        "top_action_label": "Dispatch relief",
                        "avg_hazard": 0.88,
                        "active_alert_count": 1,
                        "current_regime": "headway_collapse",
                        "current_regime_label": "Severe bunching / service gap",
                        "priority_score": 92,
                        "priority_label": "Immediate",
                        "activity_status": "active_now",
                        "activity_status_label": "Active now",
                        "activity_reason": "live_telemetry",
                        "activity_reason_label": "Live telemetry present",
                    },
                ],
                "scheduled_later_lines": [],
                "inactive_lines": [],
                "vehicles": [],
            },
            "regimes": {
                "generated_at": "2024-03-09T00:00:00+00:00",
                "regimes": [],
                "recurring_regimes": [],
            },
            "incidents": {
                "generated_at": "2024-03-09T00:00:00+00:00",
                "incidents": [
                    {
                        "incident_id": "inc-low",
                        "timestamp_ms": 1_710_000_100_000,
                        "entity_id": "route:Low:0",
                        "entity_type": "corridor",
                        "label": "Low Priority Line",
                        "route_id": "Low",
                        "severity": "warning",
                        "action": "hold",
                        "action_label": "Hold to rebalance",
                        "regime": "bunching_onset",
                        "regime_label": "Early bunching",
                        "hazard": 0.51,
                        "confidence": 0.8,
                        "priority_score": 61,
                        "priority_label": "Watch",
                        "summary": "Low priority",
                        "recommended_action": "Monitor",
                        "reasons": ["bunching_onset"],
                        "provenance": {},
                    },
                    {
                        "incident_id": "inc-high",
                        "timestamp_ms": 1_710_000_200_000,
                        "entity_id": "route:High:0",
                        "entity_type": "corridor",
                        "label": "High Priority Line",
                        "route_id": "High",
                        "severity": "critical",
                        "action": "dispatch_relief",
                        "action_label": "Dispatch relief",
                        "regime": "headway_collapse",
                        "regime_label": "Severe bunching / service gap",
                        "hazard": 0.88,
                        "confidence": 0.84,
                        "priority_score": 92,
                        "priority_label": "Immediate",
                        "summary": "High priority",
                        "recommended_action": "Dispatch relief",
                        "reasons": ["headway_collapse"],
                        "provenance": {},
                    },
                ],
            },
        }
    )

    entities = store.entities()
    incidents = store.incidents()

    assert [row["entity_id"] for row in entities["active_lines"]] == [
        "route:High:0",
        "route:Low:0",
    ]
    assert [row["priority_label"] for row in entities["active_lines"]] == [
        "Immediate",
        "Watch",
    ]
    assert [row["incident_id"] for row in incidents["incidents"]] == [
        "inc-high",
        "inc-low",
    ]


def _snapshot(*, timestamp_ms: int, delay_seconds: int, hazard: float, source: str = "live", trace_id: str | None = None):
    regime = {
        "timestamp_ms": timestamp_ms,
        "entity_id": "route:Red:0",
        "entity_type": "corridor",
        "label": "Red Line",
        "route_id": "Red",
        "regime": "bunching_onset" if hazard < 0.7 else "headway_collapse",
        "hazard": hazard,
        "action": "hold" if hazard < 0.7 else "dispatch_relief",
        "scoring_backend": "heuristic_v1",
        "confidence": 0.82,
        "signature": f"sig-{timestamp_ms}",
        "reasons": ["delay_spread"],
        "provenance": {"top_factors": [{"factor": "delay_spread", "label": "Delay spread"}]},
        "metrics": {"median_delay_seconds": delay_seconds, "compressed_headway_share": 0.75},
        "source": source,
        "collection_source": "gtfs_rt",
        "trace_id": trace_id,
    }
    observation = {
        "timestamp_ms": timestamp_ms,
        "route_id": "Red",
        "trip_id": "red-1",
        "vehicle_id": "1811",
        "vehicle_label": "1811",
        "direction_id": 0,
        "stop_id": "place-davis",
        "current_status": "IN_TRANSIT_TO",
        "occupancy_status": "MANY_SEATS_AVAILABLE",
        "delay_seconds": delay_seconds,
        "source": source,
        "collection_source": "gtfs_rt_vehicle_positions",
        "trace_id": trace_id,
    }
    return {
        "errors": [],
        "feed_status": {
            "feed_label": "MBTA",
            "updated_at": "2024-03-09T00:00:00+00:00",
            "vehicle_count": 1,
            "trip_update_count": 1,
            "alert_count": 0,
            "collection_source": "gtfs_rt",
            "status": "ok",
        },
        "health": {
            "system_name": "MBTA",
            "generated_at": "2024-03-09T00:00:00+00:00",
            "status": "warning" if hazard >= 0.5 else "ok",
            "line_count": 1,
            "active_line_count": 1,
            "scheduled_later_line_count": 0,
            "inactive_line_count": 0,
            "visible_line_count": 1,
            "vehicle_count": 1,
            "incident_count": 1,
            "critical_incidents": 1 if hazard >= 0.8 else 0,
            "avg_hazard": hazard,
            "avg_confidence": 0.82,
            "max_hazard": hazard,
            "action_counts": {"hold": 1} if hazard < 0.7 else {"dispatch_relief": 1},
            "regime_counts": {"bunching_onset": 1} if hazard < 0.7 else {"headway_collapse": 1},
            "feed_status": {
                "feed_label": "MBTA",
                "updated_at": "2024-03-09T00:00:00+00:00",
                "vehicle_count": 1,
                "trip_update_count": 1,
                "alert_count": 0,
                "collection_source": "gtfs_rt",
                "status": "ok",
            },
            "worst_corridor": regime,
        },
        "entities": {
            "generated_at": "2024-03-09T00:00:00+00:00",
            "lines": [
                {
                    "entity_id": "route:Red:0",
                    "route_id": "Red",
                    "direction_id": 0,
                    "label": "Red Line",
                    "vehicle_count": 1,
                    "median_delay_seconds": delay_seconds,
                    "scheduled_headway_seconds": 480,
                    "avg_delay_seconds": float(delay_seconds),
                    "top_action": "hold" if hazard < 0.7 else "dispatch_relief",
                    "avg_hazard": hazard,
                    "active_alert_count": 0,
                    "activity_status": "active_now",
                    "activity_reason": "live_telemetry",
                }
            ],
            "active_lines": [
                {
                    "entity_id": "route:Red:0",
                    "route_id": "Red",
                    "direction_id": 0,
                    "label": "Red Line",
                    "vehicle_count": 1,
                    "median_delay_seconds": delay_seconds,
                    "scheduled_headway_seconds": 480,
                    "avg_delay_seconds": float(delay_seconds),
                    "top_action": "hold" if hazard < 0.7 else "dispatch_relief",
                    "avg_hazard": hazard,
                    "active_alert_count": 0,
                    "activity_status": "active_now",
                    "activity_reason": "live_telemetry",
                }
            ],
            "scheduled_later_lines": [],
            "inactive_lines": [],
            "vehicles": [
                {
                    "entity_id": "vehicle:1811",
                    "label": "1811",
                    "vehicle_id": "1811",
                    "route_id": "Red",
                    "route_label": "Red Line",
                    "trip_id": "red-1",
                    "direction_id": 0,
                    "stop_id": "place-davis",
                    "status": "IN_TRANSIT_TO",
                    "delay_seconds": delay_seconds,
                    "occupancy_status": "MANY_SEATS_AVAILABLE",
                    "source": source,
                    "collection_source": "gtfs_rt_vehicle_positions",
                    "corridor_entity_id": "route:Red:0",
                    "regime": regime,
                    "observation": observation,
                }
            ],
        },
        "regimes": {
            "generated_at": "2024-03-09T00:00:00+00:00",
            "regimes": [regime],
            "recurring_regimes": [],
        },
        "incidents": {
            "generated_at": "2024-03-09T00:00:00+00:00",
            "incidents": [
                {
                    "incident_id": f"route:Red:0:{timestamp_ms}",
                    "timestamp_ms": timestamp_ms,
                    "entity_id": "route:Red:0",
                    "entity_type": "corridor",
                    "label": "Red Line",
                    "route_id": "Red",
                    "severity": "warning",
                    "action": "hold" if hazard < 0.7 else "dispatch_relief",
                    "regime": "bunching_onset" if hazard < 0.7 else "headway_collapse",
                    "hazard": hazard,
                    "confidence": 0.82,
                    "summary": "Summary",
                    "recommended_action": "Act",
                    "reasons": ["delay_spread"],
                    "provenance": {"top_factors": [{"factor": "delay_spread", "label": "Delay spread"}]},
                    "source": source,
                    "trace_id": trace_id,
                }
            ],
        },
    }
