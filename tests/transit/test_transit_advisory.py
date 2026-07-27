import math
from pathlib import Path

import pytest

from scripts.transit.advisory import (
    AdvisoryPolicy,
    AdvisoryRequest,
    AlternativeServiceAdvisor,
    ExplicitTransfer,
    RealtimePredictionIndex,
    RouteHealth,
    TransitTopology,
    compile_transit_topology,
    load_transit_topology,
    save_transit_topology,
)
from scripts.transit.transit_types import (
    GTFSRoute,
    GTFSStaticCatalog,
    GTFSStop,
    GTFSStopTime,
    GTFSTransfer,
    GTFSTrip,
    TransitRealtimeBundle,
    TransitStopTimeUpdate,
    TransitTripUpdateObservation,
)


NOW_MS = 1_800_000_000_000


def _time(minutes: int) -> int:
    return int(NOW_MS / 1000) + (minutes * 60)


def _catalog(
    trip_specs: dict[str, tuple[str, int | None, tuple[str, ...]]],
    *,
    transfers: list[GTFSTransfer] | None = None,
    stops: dict[str, GTFSStop] | None = None,
) -> GTFSStaticCatalog:
    stop_ids = sorted(
        {
            stop_id
            for _, _, trip_stops in trip_specs.values()
            for stop_id in trip_stops
        }
    )
    routes = sorted({route_id for route_id, _, _ in trip_specs.values()})
    return GTFSStaticCatalog(
        feed_label="test",
        routes={
            route_id: GTFSRoute(
                route_id=route_id,
                route_short_name=route_id,
                route_type=1 if route_id == "R" else 3,
            )
            for route_id in routes
        },
        trips={
            trip_id: GTFSTrip(
                trip_id=trip_id,
                route_id=route_id,
                direction_id=direction_id,
            )
            for trip_id, (route_id, direction_id, _) in trip_specs.items()
        },
        stops=stops
        or {
            stop_id: GTFSStop(stop_id=stop_id, stop_name=f"Stop {stop_id}")
            for stop_id in stop_ids
        },
        stop_times_by_trip={
            trip_id: [
                GTFSStopTime(
                    trip_id=trip_id,
                    stop_id=stop_id,
                    stop_sequence=index,
                    arrival_time=f"08:{(index - 1) * 10:02d}:00",
                    departure_time=f"08:{(index - 1) * 10:02d}:00",
                )
                for index, stop_id in enumerate(trip_stops, 1)
            ]
            for trip_id, (_, _, trip_stops) in trip_specs.items()
        },
        transfers=transfers or [],
    )


def _evidence(
    catalog: GTFSStaticCatalog,
    trip_times: dict[str, tuple[int, ...]],
    *,
    observed_at_ms: int = NOW_MS - 10_000,
    relationships: dict[tuple[str, str], str] | None = None,
    trip_relationships: dict[str, str] | None = None,
) -> dict:
    events = []
    relationships = relationships or {}
    trip_relationships = trip_relationships or {}
    trip_descriptors = []
    for trip_id, minutes in trip_times.items():
        trip = catalog.trips[trip_id]
        trip_relationship = trip_relationships.get(trip_id)
        trip_descriptors.append(
            {
                "route_id": trip.route_id,
                "trip_id": trip_id,
                "direction_id": trip.direction_id,
                "schedule_relationship": trip_relationship,
                "trip_update_timestamp_ms": observed_at_ms,
            }
        )
        stop_times = catalog.stop_times_by_trip[trip_id]
        for stop_time, minute in zip(stop_times, minutes):
            relationship = relationships.get((trip_id, stop_time.stop_id))
            event_time = None if relationship in {"SKIPPED", "NO_DATA"} else _time(minute)
            events.append(
                {
                    "route_id": trip.route_id,
                    "trip_id": trip_id,
                    "direction_id": trip.direction_id,
                    "stop_id": stop_time.stop_id,
                    "stop_sequence": stop_time.stop_sequence,
                    "arrival_time_unix": event_time,
                    "departure_time_unix": event_time,
                    "arrival_time_source": "gtfs_rt_time" if event_time else None,
                    "departure_time_source": "gtfs_rt_time" if event_time else None,
                    "schedule_relationship": relationship,
                    "trip_schedule_relationship": trip_relationship,
                    "trip_update_timestamp_ms": observed_at_ms,
                    "collection_source": "gtfs_rt_trip_updates",
                }
            )
    return {
        "schema_version": "sentinel.prediction_evidence.v1",
        "snapshot_timestamp_ms": NOW_MS,
        "feed_timestamp_ms": observed_at_ms,
        "trip_descriptors": trip_descriptors,
        "events": events,
    }


def _health(*routes: tuple[str, int | None, float]) -> dict:
    output = {}
    for route_id, direction_id, score in routes:
        health = RouteHealth(
            route_id=route_id,
            direction_id=direction_id,
            disruption_score=score,
            confidence=0.9,
            observed_at_ms=NOW_MS - 10_000,
            regime="service_degraded" if score >= 0.65 else "healthy",
        )
        output[(route_id, direction_id)] = health
    return output


def test_topology_compiler_orders_rides_and_honors_transfer_rules(tmp_path: Path):
    stops = {
        "O": GTFSStop("O", "Origin", 34.0000, -118.0000),
        "P1": GTFSStop("P1", "Platform 1", 34.0010, -118.0000, parent_station="station"),
        "P2": GTFSStop("P2", "Platform 2", 34.0011, -118.0000, parent_station="station"),
        "D": GTFSStop("D", "Destination", 34.0200, -118.0000),
    }
    catalog = _catalog(
        {
            "a-1": ("A", 0, ("O", "P1", "D")),
            "a-2": ("A", 0, ("O", "P1", "D")),
            "r-1": ("R", 1, ("P2", "D")),
        },
        stops=stops,
        transfers=[
            GTFSTransfer("P1", "P2", transfer_type=2, min_transfer_time=240),
            GTFSTransfer("P2", "O", transfer_type=3, min_transfer_time=60),
        ],
    )
    topology = compile_transit_topology(catalog, max_nearby_walk_meters=0)

    assert len(topology.ride_edges) == 3
    assert [(edge.from_stop_id, edge.to_stop_id) for edge in topology.ride_edges if edge.route_id == "A"] == [
        ("O", "P1"),
        ("P1", "D"),
    ]
    assert topology.trip_paths["a-1"].stops is topology.trip_paths["a-2"].stops
    assert any(
        edge.from_stop_id == "P1"
        and edge.to_stop_id == "P2"
        and edge.minimum_transfer_seconds == 240
        and edge.source == "agency_defined"
        for edge in topology.transfer_edges
    )
    assert not any(
        edge.from_stop_id == "P2" and edge.to_stop_id == "O"
        for edge in topology.transfer_edges
    )

    configured = compile_transit_topology(
        catalog,
        explicit_transfers=[ExplicitTransfer("P1", "P2", 120)],
        max_nearby_walk_meters=0,
    )
    configured_edge = next(
        edge
        for edge in configured.transfer_edges
        if edge.from_stop_id == "P1" and edge.to_stop_id == "P2"
    )
    assert configured_edge.source == "configured"
    assert configured_edge.minimum_transfer_seconds == 120

    artifact = tmp_path / "topology.json.gz"
    save_transit_topology(topology, artifact, metadata={"trip_count": 3})
    first_bytes = artifact.read_bytes()
    save_transit_topology(topology, artifact, metadata={"trip_count": 3})
    assert artifact.read_bytes() == first_bytes
    restored = load_transit_topology(artifact)
    assert restored.to_dict() == topology.to_dict()
    assert restored.trip_paths["a-1"].stops is restored.trip_paths["a-2"].stops


def test_topology_artifact_rejects_pattern_without_route_id():
    topology = compile_transit_topology(
        _catalog({"a-1": ("A", 0, ("O", "D"))})
    )
    payload = topology.to_dict()
    payload["patterns"][0].pop("route_id")

    with pytest.raises(ValueError, match="missing route_id"):
        TransitTopology.from_dict(payload)


def test_transfer_tombstones_remove_and_prevent_directed_inferred_edges():
    stops = {
        "A": GTFSStop("A", "A", 34.0000, -118.0000),
        "B": GTFSStop("B", "B", 34.0005, -118.0000),
    }
    catalog = _catalog(
        {
            "a-1": ("A", 0, ("A", "B")),
            "b-1": ("B", 0, ("B", "A")),
        },
        stops=stops,
        transfers=[GTFSTransfer("A", "B", transfer_type=3)],
    )

    agency_blocked = compile_transit_topology(
        catalog,
        explicit_transfers=[ExplicitTransfer("A", "B", 15)],
        max_nearby_walk_meters=100,
    )
    pairs = {(edge.from_stop_id, edge.to_stop_id) for edge in agency_blocked.transfer_edges}
    assert ("A", "B") not in pairs
    assert ("B", "A") in pairs

    explicitly_blocked = compile_transit_topology(
        catalog,
        explicit_transfers=[ExplicitTransfer("B", "A", permitted=False)],
        max_nearby_walk_meters=100,
    )
    pairs = {(edge.from_stop_id, edge.to_stop_id) for edge in explicitly_blocked.transfer_edges}
    assert ("A", "B") not in pairs
    assert ("B", "A") not in pairs


def test_agency_recommended_transfers_keep_defensible_different_stop_duration():
    stops = {
        "A": GTFSStop("A", "A", 34.0000, -118.0000),
        "B": GTFSStop("B", "B", 34.0010, -118.0000),
        "N1": GTFSStop("N1", "No coordinates 1"),
        "N2": GTFSStop("N2", "No coordinates 2"),
        "S1": GTFSStop("S1", "Station 1", 34.0020, -118.0000, parent_station="S"),
        "S2": GTFSStop("S2", "Station 2", 34.0021, -118.0000, parent_station="S"),
    }
    catalog = _catalog(
        {"a-1": ("A", 0, ("A", "B", "N1", "N2", "S1", "S2"))},
        stops=stops,
        transfers=[
            GTFSTransfer("A", "B", transfer_type=0),
            GTFSTransfer("N1", "N2", transfer_type=1),
            GTFSTransfer("B", "N1", transfer_type=2, min_transfer_time=0),
            GTFSTransfer("S1", "S2", transfer_type=0),
            GTFSTransfer("B", "B", transfer_type=0),
        ],
    )

    topology = compile_transit_topology(
        catalog,
        explicit_transfers=[ExplicitTransfer("N2", "N1")],
        max_nearby_walk_meters=0,
        walking_speed_mps=1.0,
        station_transfer_floor_seconds=60,
    )
    transfers = {
        (edge.from_stop_id, edge.to_stop_id): edge for edge in topology.transfer_edges
    }
    spatial = transfers[("A", "B")]
    assert spatial.source == "agency_defined"
    assert spatial.minimum_transfer_seconds == math.ceil(spatial.distance_meters or 0)
    assert spatial.minimum_transfer_seconds > 0
    assert transfers[("N1", "N2")].minimum_transfer_seconds == 60
    assert transfers[("B", "N1")].minimum_transfer_seconds == 60
    assert transfers[("S1", "S2")].minimum_transfer_seconds == 60
    assert transfers[("N2", "N1")].minimum_transfer_seconds == 60
    assert transfers[("N2", "N1")].source == "configured"
    assert transfers[("B", "B")].minimum_transfer_seconds == 0


def test_direct_advisory_uses_live_predictions_and_explainable_health():
    catalog = _catalog(
        {
            "a-1": ("A", 0, ("O", "X", "D")),
            "c-1": ("C", 0, ("O", "X", "D")),
        }
    )
    topology = compile_transit_topology(catalog, max_nearby_walk_meters=0)
    advisor = AlternativeServiceAdvisor(topology)

    decision = advisor.recommend(
        AdvisoryRequest("O", "D", "A", NOW_MS, direction_id=0),
        _evidence(catalog, {"a-1": (5, 20, 30), "c-1": (4, 10, 15)}),
        _health(("A", 0, 0.9), ("C", 0, 0.1)),
    )

    assert decision.status == "published"
    advisory = decision.advisories[0]
    assert advisory.route_ids == ("C",)
    assert advisory.expected_time_saved_seconds == 15 * 60
    assert advisory.total_walking_seconds == 0
    assert NOW_MS < advisory.expires_at_ms <= NOW_MS + 120_000
    assert "estimated" in advisory.summary
    assert "mechanical" not in advisory.summary.lower()
    prediction_evidence = next(
        row for row in advisory.evidence if row.kind == "alternative_prediction"
    )
    assert prediction_evidence.details["arrival_time_source"] == "gtfs_rt_time"
    assert prediction_evidence.details["route_disruption_score"] == 0.1


def test_one_transfer_advisory_is_bounded_and_records_inferred_transfer():
    catalog = _catalog(
        {
            "a-1": ("A", 0, ("O", "D")),
            "c-1": ("C", 0, ("O", "T")),
            "r-1": ("R", 1, ("T", "D")),
        }
    )
    topology = compile_transit_topology(catalog, max_nearby_walk_meters=0)
    decision = AlternativeServiceAdvisor(topology).recommend(
        AdvisoryRequest("O", "D", "A", NOW_MS, direction_id=0),
        _evidence(catalog, {"a-1": (5, 30), "c-1": (2, 8), "r-1": (10, 16)}),
        _health(("A", 0, 0.9), ("C", 0, 0.1), ("R", 1, 0.05)),
    )

    assert decision.status == "published"
    advisory = decision.advisories[0]
    assert advisory.route_ids == ("C", "R")
    assert [leg.kind for leg in advisory.legs] == ["ride", "transfer", "ride"]
    assert advisory.total_walking_seconds == 0
    assert advisory.total_transfer_seconds == 90
    transfer = next(row for row in advisory.evidence if row.kind == "transfer")
    assert transfer.details["source"] == "inferred_shared_stop"
    assert transfer.details["provenance"] == "inferred"


@pytest.mark.parametrize(
    ("health", "evidence_kwargs", "policy", "reason"),
    [
        (
            _health(("A", 0, 0.4), ("C", 0, 0.1)),
            {},
            AdvisoryPolicy(),
            "disruption_below_threshold",
        ),
        (
            _health(("A", 0, 0.9), ("C", 0, 0.1)),
            {"observed_at_ms": NOW_MS - 121_000},
            AdvisoryPolicy(),
            "no_fresh_realtime_predictions",
        ),
        (
            _health(("A", 0, 0.9), ("C", 0, 0.1)),
            {},
            AdvisoryPolicy(minimum_benefit_seconds=1_000),
            "no_materially_better_reliable_alternative",
        ),
    ],
)
def test_advisory_strictly_suppresses_weak_inputs(health, evidence_kwargs, policy, reason):
    catalog = _catalog(
        {
            "a-1": ("A", 0, ("O", "X", "D")),
            "c-1": ("C", 0, ("O", "X", "D")),
        }
    )
    topology = compile_transit_topology(catalog, max_nearby_walk_meters=0)
    evidence = _evidence(
        catalog,
        {"a-1": (5, 20, 30), "c-1": (4, 10, 15)},
        **evidence_kwargs,
    )

    decision = AlternativeServiceAdvisor(topology, policy=policy).recommend(
        AdvisoryRequest("O", "D", "A", NOW_MS, direction_id=0),
        evidence,
        health,
    )

    assert decision.status == "suppressed"
    assert decision.suppression_reasons == (reason,)


@pytest.mark.parametrize("relationship", ["CANCELED", "DELETED"])
def test_non_running_trip_cannot_supply_disrupted_route_baseline(relationship):
    catalog = _catalog(
        {
            "a-1": ("A", 0, ("O", "D")),
            "c-1": ("C", 0, ("O", "D")),
        }
    )
    topology = compile_transit_topology(catalog, max_nearby_walk_meters=0)
    evidence = _evidence(
        catalog,
        {"a-1": (5, 30), "c-1": (4, 15)},
        trip_relationships={"a-1": relationship},
    )

    index = RealtimePredictionIndex.from_evidence(evidence, topology)
    decision = AlternativeServiceAdvisor(topology).recommend(
        AdvisoryRequest("O", "D", "A", NOW_MS, direction_id=0),
        index,
        _health(("A", 0, 0.9), ("C", 0, 0.1)),
    )

    assert index.blocked_trip_ids == frozenset({"a-1"})
    assert "a-1" not in index.by_trip
    assert decision.status == "suppressed"
    assert decision.suppression_reasons == (
        "no_reliable_disrupted_route_baseline",
    )


@pytest.mark.parametrize(
    "relationship",
    [
        "CANCELED",
        "DELETED",
        "REPLACEMENT",
        "DUPLICATED",
        "NEW",
        "UNKNOWN_FUTURE_VALUE",
    ],
)
def test_non_running_or_unsupported_trip_cannot_supply_alternative(relationship):
    catalog = _catalog(
        {
            "a-1": ("A", 0, ("O", "D")),
            "c-1": ("C", 0, ("O", "D")),
        }
    )
    topology = compile_transit_topology(catalog, max_nearby_walk_meters=0)
    evidence = _evidence(
        catalog,
        {"a-1": (5, 30), "c-1": (4, 15)},
        trip_relationships={"c-1": relationship},
    )

    index = RealtimePredictionIndex.from_evidence(evidence, topology)
    decision = AlternativeServiceAdvisor(topology).recommend(
        AdvisoryRequest("O", "D", "A", NOW_MS, direction_id=0),
        index,
        _health(("A", 0, 0.9), ("C", 0, 0.1)),
    )

    assert index.blocked_trip_ids == frozenset({"c-1"})
    assert "c-1" not in index.by_trip
    assert decision.status == "suppressed"
    assert decision.suppression_reasons == (
        "no_materially_better_reliable_alternative",
    )
    assert decision.evaluated_candidate_count == 0


def test_bundle_index_drops_canceled_trip_predictions_before_segment_search():
    catalog = _catalog({"a-1": ("A", 0, ("O", "D"))})
    topology = compile_transit_topology(catalog, max_nearby_walk_meters=0)
    bundle = TransitRealtimeBundle(
        feed_label="test",
        feed_timestamp_ms=NOW_MS - 10_000,
        trip_updates=[
            TransitTripUpdateObservation(
                timestamp_ms=NOW_MS - 10_000,
                route_id="A",
                trip_id="a-1",
                direction_id=0,
                schedule_relationship="CANCELED",
                stop_time_updates=[
                    TransitStopTimeUpdate(
                        stop_id="O",
                        stop_sequence=1,
                        arrival_time_unix=_time(5),
                        departure_time_unix=_time(5),
                    ),
                    TransitStopTimeUpdate(
                        stop_id="D",
                        stop_sequence=2,
                        arrival_time_unix=_time(30),
                        departure_time_unix=_time(30),
                    ),
                ],
            )
        ],
    )

    index = RealtimePredictionIndex.from_bundle(topology, bundle)

    assert index.by_trip == {}
    assert index.blocked_trip_ids == frozenset({"a-1"})


def test_newer_scheduled_descriptor_does_not_reuse_older_canceled_rows():
    catalog = _catalog({"a-1": ("A", 0, ("O", "D"))})
    topology = compile_transit_topology(catalog, max_nearby_walk_meters=0)
    evidence = _evidence(
        catalog,
        {"a-1": (5, 30)},
        observed_at_ms=NOW_MS - 20_000,
        trip_relationships={"a-1": "CANCELED"},
    )
    evidence["trip_descriptors"].append(
        {
            "route_id": "A",
            "trip_id": "a-1",
            "direction_id": 0,
            "schedule_relationship": "SCHEDULED",
            "trip_update_timestamp_ms": NOW_MS - 10_000,
        }
    )

    index = RealtimePredictionIndex.from_evidence(evidence, topology)

    assert index.blocked_trip_ids == frozenset()
    assert index.by_trip == {}


def test_bundle_does_not_reuse_older_unsupported_rows_after_resumption():
    catalog = _catalog({"a-1": ("A", 0, ("O", "D"))})
    topology = compile_transit_topology(catalog, max_nearby_walk_meters=0)
    unsupported_with_times = TransitTripUpdateObservation(
        timestamp_ms=NOW_MS - 20_000,
        route_id="A",
        trip_id="a-1",
        direction_id=0,
        schedule_relationship="REPLACEMENT",
        stop_time_updates=[
            TransitStopTimeUpdate(
                stop_id="O",
                stop_sequence=1,
                arrival_time_unix=_time(5),
                departure_time_unix=_time(5),
            ),
            TransitStopTimeUpdate(
                stop_id="D",
                stop_sequence=2,
                arrival_time_unix=_time(30),
                departure_time_unix=_time(30),
            ),
        ],
    )
    resumed_without_times = TransitTripUpdateObservation(
        timestamp_ms=NOW_MS - 10_000,
        route_id="A",
        trip_id="a-1",
        direction_id=0,
        schedule_relationship="SCHEDULED",
    )
    bundle = TransitRealtimeBundle(
        feed_label="test",
        feed_timestamp_ms=NOW_MS - 10_000,
        trip_updates=[unsupported_with_times, resumed_without_times],
    )

    index = RealtimePredictionIndex.from_bundle(topology, bundle)

    assert index.blocked_trip_ids == frozenset()
    assert index.by_trip == {}


def test_direction_health_does_not_leak_across_opposite_corridor():
    catalog = _catalog(
        {
            "a-out": ("A", 0, ("O", "D")),
            "a-in": ("A", 1, ("D", "O")),
            "c-out": ("C", 0, ("O", "D")),
        }
    )
    topology = compile_transit_topology(catalog, max_nearby_walk_meters=0)
    evidence = _evidence(catalog, {"a-out": (5, 30), "c-out": (4, 15)})
    opposite_only = _health(("A", 1, 0.9), ("C", 0, 0.1))

    decision = AlternativeServiceAdvisor(topology).recommend(
        AdvisoryRequest("O", "D", "A", NOW_MS, direction_id=0),
        evidence,
        opposite_only,
    )

    assert decision.status == "suppressed"
    assert decision.suppression_reasons == ("missing_disruption_health",)


def test_unsupported_prediction_evidence_schema_fails_closed():
    catalog = _catalog({"a-1": ("A", 0, ("O", "D"))})
    topology = compile_transit_topology(catalog, max_nearby_walk_meters=0)

    decision = AlternativeServiceAdvisor(topology).recommend(
        AdvisoryRequest("O", "D", "A", NOW_MS, direction_id=0),
        {"schema_version": "sentinel.prediction_evidence.v2", "events": []},
        _health(("A", 0, 0.9)),
    )

    assert decision.status == "suppressed"
    assert decision.suppression_reasons == ("invalid_realtime_prediction_evidence",)


def test_no_data_reduces_coverage_without_claiming_a_skipped_stop():
    catalog = _catalog({"a-1": ("A", 0, ("O", "X", "D"))})
    topology = compile_transit_topology(catalog, max_nearby_walk_meters=0)
    evidence = _evidence(
        catalog,
        {"a-1": (5, 20, 30)},
        relationships={("a-1", "X"): "NO_DATA"},
    )

    index = RealtimePredictionIndex.from_evidence(evidence, topology)

    assert index.skipped_trip_stops == frozenset()
    assert index.no_data_trip_stops == frozenset({("a-1", "X", 2)})


@pytest.mark.parametrize("relationship", ["NO_DATA", "SKIPPED"])
def test_missing_alternative_stop_predictions_suppress_low_coverage(relationship):
    catalog = _catalog(
        {
            "a-1": ("A", 0, ("O", "W", "X", "D")),
            "c-1": ("C", 0, ("O", "W", "X", "D")),
        }
    )
    topology = compile_transit_topology(catalog, max_nearby_walk_meters=0)
    evidence = _evidence(
        catalog,
        {"a-1": (5, 12, 20, 30), "c-1": (4, 8, 11, 15)},
        relationships={
            ("c-1", "W"): relationship,
            ("c-1", "X"): relationship,
        },
    )

    decision = AlternativeServiceAdvisor(topology).recommend(
        AdvisoryRequest("O", "D", "A", NOW_MS, direction_id=0),
        evidence,
        _health(("A", 0, 0.9), ("C", 0, 0.1)),
    )

    assert decision.status == "suppressed"
    assert decision.evaluated_candidate_count == 0


@pytest.mark.parametrize("stop_id", ["O", "D"])
def test_skipped_alternative_endpoints_are_never_recommended(stop_id):
    catalog = _catalog(
        {
            "a-1": ("A", 0, ("O", "D")),
            "c-1": ("C", 0, ("O", "D")),
        }
    )
    topology = compile_transit_topology(catalog, max_nearby_walk_meters=0)
    evidence = _evidence(
        catalog,
        {"a-1": (5, 30), "c-1": (4, 15)},
        relationships={("c-1", stop_id): "SKIPPED"},
    )

    decision = AlternativeServiceAdvisor(topology).recommend(
        AdvisoryRequest("O", "D", "A", NOW_MS, direction_id=0),
        evidence,
        _health(("A", 0, 0.9), ("C", 0, 0.1)),
    )

    assert decision.status == "suppressed"
    assert decision.evaluated_candidate_count == 0


def test_degraded_alternative_is_suppressed():
    catalog = _catalog(
        {
            "a-1": ("A", 0, ("O", "D")),
            "c-1": ("C", 0, ("O", "D")),
        }
    )
    topology = compile_transit_topology(catalog, max_nearby_walk_meters=0)

    decision = AlternativeServiceAdvisor(topology).recommend(
        AdvisoryRequest("O", "D", "A", NOW_MS, direction_id=0),
        _evidence(catalog, {"a-1": (5, 30), "c-1": (4, 15)}),
        _health(("A", 0, 0.9), ("C", 0, 0.5)),
    )

    assert decision.status == "suppressed"
    assert decision.evaluated_candidate_count == 0


def test_health_penalty_ranks_a_healthier_route_ahead_of_a_slightly_faster_one():
    catalog = _catalog(
        {
            "a-1": ("A", 0, ("O", "D")),
            "c-1": ("C", 0, ("O", "D")),
            "d-1": ("D2", 0, ("O", "D")),
        }
    )
    topology = compile_transit_topology(catalog, max_nearby_walk_meters=0)

    decision = AlternativeServiceAdvisor(topology).recommend(
        AdvisoryRequest("O", "D", "A", NOW_MS, direction_id=0),
        _evidence(catalog, {"a-1": (5, 30), "c-1": (4, 14), "d-1": (5, 16)}),
        _health(("A", 0, 0.9), ("C", 0, 0.35), ("D2", 0, 0.0)),
    )

    assert decision.status == "published"
    assert decision.advisories[0].route_ids == ("D2",)


def test_itinerary_level_walking_limit_suppresses_accumulated_walk():
    stops = {
        "O": GTFSStop("O", "Origin", 34.0000, -118.0000),
        "O2": GTFSStop("O2", "Origin alternate", 34.0010, -118.0000),
        "D2": GTFSStop("D2", "Destination alternate", 34.0090, -118.0000),
        "D": GTFSStop("D", "Destination", 34.0100, -118.0000),
    }
    catalog = _catalog(
        {
            "a-1": ("A", 0, ("O", "D")),
            "c-1": ("C", 0, ("O2", "D2")),
        },
        stops=stops,
        transfers=[
            GTFSTransfer("O", "O2", transfer_type=2, min_transfer_time=240),
            GTFSTransfer("D2", "D", transfer_type=2, min_transfer_time=240),
        ],
    )
    topology = compile_transit_topology(catalog, max_nearby_walk_meters=0)
    policy = AdvisoryPolicy(maximum_walking_seconds=400)

    decision = AlternativeServiceAdvisor(topology, policy=policy).recommend(
        AdvisoryRequest("O", "D", "A", NOW_MS, direction_id=0),
        _evidence(catalog, {"a-1": (5, 40), "c-1": (5, 15)}),
        _health(("A", 0, 0.9), ("C", 0, 0.1)),
    )

    assert decision.status == "suppressed"
    assert decision.evaluated_candidate_count == 0


def test_physical_connection_uses_effective_transfer_time_for_walk_cap():
    catalog = _catalog(
        {
            "a-1": ("A", 0, ("O", "D")),
            "c-1": ("C", 0, ("O", "T1")),
            "r-1": ("R", 1, ("T2", "D")),
        },
        transfers=[GTFSTransfer("T1", "T2", transfer_type=0)],
    )
    topology = compile_transit_topology(catalog, max_nearby_walk_meters=0)
    policy = AdvisoryPolicy(maximum_walking_seconds=60, minimum_transfer_seconds=90)

    decision = AlternativeServiceAdvisor(topology, policy=policy).recommend(
        AdvisoryRequest("O", "D", "A", NOW_MS, direction_id=0),
        _evidence(catalog, {"a-1": (5, 30), "c-1": (2, 8), "r-1": (10, 16)}),
        _health(("A", 0, 0.9), ("C", 0, 0.1), ("R", 1, 0.05)),
    )

    assert decision.status == "suppressed"
    assert decision.evaluated_candidate_count == 0
