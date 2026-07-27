import json
import os
import threading
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from scripts.transit.advisory import (
    RideEdge,
    TripPath,
    TripStop,
    build_transit_topology,
    save_transit_topology,
)
from scripts.transit.api import (
    ADVISORY_PRODUCT_BOUNDARY,
    AdvisoryRequestError,
    TransitAPIService,
    build_parser,
    start_transit_http_server,
)
from scripts.transit.auth import reload_registry
from scripts.transit.transit_types import GTFSStop


NOW_MS = 1_710_000_000_000


class _AdvisoryStore:
    def prediction_evidence(self, *, scope="all", trace_id=None):
        assert scope == "live"
        assert trace_id is None
        return {
            "schema_version": "sentinel.prediction_evidence.v1",
            "feed_timestamp_ms": NOW_MS,
            "events": [
                _prediction("a-trip", "A", "origin", 1, 60, direction_id=0),
                _prediction("a-trip", "A", "destination", 2, 900, direction_id=0),
                _prediction("b-trip", "B", "origin", 1, 120, direction_id=0),
                _prediction("b-trip", "B", "destination", 2, 300, direction_id=0),
            ],
        }

    def regimes(self, *, scope="all", trace_id=None):
        assert scope == "live"
        assert trace_id is None
        return {
            "regimes": [
                _regime("A", 0, hazard=0.9, regime="service_degraded"),
                _regime("A", 1, hazard=0.05, regime="healthy"),
                _regime("B", 0, hazard=0.1, regime="healthy"),
            ]
        }


class _NoReadStore:
    def prediction_evidence(self, **_kwargs):  # pragma: no cover - assertion path
        raise AssertionError("unconfigured endpoint must not read live evidence")

    def regimes(self, **_kwargs):  # pragma: no cover - assertion path
        raise AssertionError("unconfigured endpoint must not read live regimes")


def _prediction(trip_id, route_id, stop_id, sequence, offset_seconds, *, direction_id):
    event_time = NOW_MS // 1000 + offset_seconds
    return {
        "trip_id": trip_id,
        "route_id": route_id,
        "direction_id": direction_id,
        "stop_id": stop_id,
        "stop_sequence": sequence,
        "arrival_time_unix": event_time,
        "departure_time_unix": event_time,
        "trip_update_timestamp_ms": NOW_MS,
        "arrival_time_source": "gtfs_rt_time",
        "departure_time_source": "gtfs_rt_time",
        "collection_source": "gtfs_rt_trip_updates",
    }


def _regime(route_id, direction_id, *, hazard, regime):
    return {
        "entity_id": f"route:{route_id}:{direction_id}",
        "entity_type": "corridor",
        "route_id": route_id,
        "regime": regime,
        "hazard": hazard,
        "confidence": 0.9,
        "timestamp_ms": NOW_MS,
        "metrics": {"direction_id": direction_id},
    }


def _topology(*, ambiguous_disrupted_direction=False):
    trip_paths = {
        "a-trip": TripPath(
            trip_id="a-trip",
            route_id="A",
            direction_id=0,
            stops=(TripStop("origin", 1), TripStop("destination", 2)),
        ),
        "b-trip": TripPath(
            trip_id="b-trip",
            route_id="B",
            direction_id=0,
            stops=(TripStop("origin", 1), TripStop("destination", 2)),
        ),
    }
    if ambiguous_disrupted_direction:
        trip_paths["a-trip-opposite"] = TripPath(
            trip_id="a-trip-opposite",
            route_id="A",
            direction_id=1,
            stops=(TripStop("origin", 1), TripStop("destination", 2)),
        )
    ride_edges = [
        RideEdge(
            from_stop_id="origin",
            to_stop_id="destination",
            route_id=path.route_id,
            direction_id=path.direction_id,
            trip_id=path.trip_id,
            from_stop_sequence=1,
            to_stop_sequence=2,
            scheduled_travel_seconds=600,
        )
        for path in trip_paths.values()
    ]
    return build_transit_topology(
        feed_label="api-test",
        stops={
            "origin": GTFSStop("origin", "Origin"),
            "destination": GTFSStop("destination", "Destination"),
        },
        route_labels={"A": "Line A", "B": "Line B"},
        route_types={"A": 3, "B": 3},
        ride_edges=ride_edges,
        transfer_edges=(),
        trip_paths=trip_paths,
    )


def test_advisory_service_publishes_with_directional_health_and_boundary(tmp_path):
    artifact = tmp_path / "topology.json.gz"
    save_transit_topology(_topology(), artifact)
    service = TransitAPIService(
        "redis://unused",
        store=_AdvisoryStore(),
        advisory_topology_path=artifact,
    )

    payload = service.transit_alternative_advisories(
        origin_stop_id="origin",
        destination_stop_id="destination",
        disrupted_route_id="A",
        requested_at_ms=NOW_MS,
    )

    assert payload["status"] == "published"
    assert payload["origin_stop_id"] == "origin"
    assert payload["destination_stop_id"] == "destination"
    assert payload["disrupted_route_id"] == "A"
    assert payload["release_stage"] == "operator_preview"
    assert payload["resolved_direction_id"] == 0
    assert payload["advisories"][0]["route_ids"] == ("B",)
    assert payload["advisories"][0]["expected_time_saved_seconds"] == 600
    assert payload["advisories"][0]["total_transfer_seconds"] == 0
    assert payload["product_boundary"] == ADVISORY_PRODUCT_BOUNDARY
    assert payload["product_boundary"]["infers_cause"] is False
    assert payload["product_boundary"]["guarantees_arrival"] is False
    assert payload["product_boundary"]["issues_dispatch_instructions"] is False


def test_advisory_options_return_route_scoped_ordered_stops(tmp_path):
    artifact = tmp_path / "topology.json.gz"
    save_transit_topology(_topology(), artifact)
    service = TransitAPIService(
        "redis://unused",
        store=_NoReadStore(),
        advisory_topology_path=artifact,
    )

    payload = service.transit_alternative_advisory_options(
        disrupted_route_id="A",
        requested_at_ms=NOW_MS,
    )

    assert payload["status"] == "available"
    assert payload["route_label"] == "Line A"
    assert payload["resolved_direction_id"] == 0
    assert payload["directions"] == [{"direction_id": 0, "label": "Direction 0"}]
    assert payload["stops"] == [
        {
            "stop_id": "origin",
            "stop_name": "Origin",
            "sequence": 1,
            "downstream_stop_ids": ["destination"],
        },
        {
            "stop_id": "destination",
            "stop_name": "Destination",
            "sequence": 2,
            "downstream_stop_ids": [],
        },
    ]
    assert payload["release_stage"] == "operator_preview"
    assert payload["product_boundary"] == ADVISORY_PRODUCT_BOUNDARY


def test_advisory_options_require_direction_only_when_route_is_ambiguous(tmp_path):
    artifact = tmp_path / "topology.json"
    save_transit_topology(_topology(ambiguous_disrupted_direction=True), artifact)
    service = TransitAPIService(
        "redis://unused",
        store=_NoReadStore(),
        advisory_topology_path=artifact,
    )

    selection = service.transit_alternative_advisory_options(
        disrupted_route_id="A",
        requested_at_ms=NOW_MS,
    )
    resolved = service.transit_alternative_advisory_options(
        disrupted_route_id="A",
        direction_id=1,
        requested_at_ms=NOW_MS,
    )

    assert selection["status"] == "selection_required"
    assert selection["suppression_reasons"] == ["direction_required"]
    assert selection["stops"] == []
    assert [row["direction_id"] for row in selection["directions"]] == [0, 1]
    assert resolved["status"] == "available"
    assert resolved["resolved_direction_id"] == 1
    assert len(resolved["stops"]) == 2


def test_advisory_options_fail_closed_without_topology():
    service = TransitAPIService(
        "redis://unused", store=_NoReadStore(), advisory_topology_path=None
    )

    payload = service.transit_alternative_advisory_options(
        disrupted_route_id="A",
        requested_at_ms=NOW_MS,
    )

    assert payload["status"] == "unavailable"
    assert payload["route_label"] is None
    assert payload["stops"] == []
    assert payload["suppression_reasons"] == ["topology_not_configured"]


def test_advisory_options_fail_closed_for_invalid_pattern_stop_reference(tmp_path):
    topology = _topology()
    topology.trip_paths["a-trip"] = TripPath(
        trip_id="a-trip",
        route_id="A",
        direction_id=0,
        stops=(TripStop("origin", 1), TripStop("missing-stop", 2)),
    )
    artifact = tmp_path / "invalid-topology.json"
    save_transit_topology(topology, artifact)
    service = TransitAPIService(
        "redis://unused", store=_NoReadStore(), advisory_topology_path=artifact
    )

    payload = service.transit_alternative_advisory_options(
        disrupted_route_id="A", requested_at_ms=NOW_MS
    )

    assert payload["status"] == "unavailable"
    assert payload["stops"] == []
    assert payload["suppression_reasons"] == ["topology_invalid"]


def test_advisory_options_preserve_valid_pairs_across_branched_patterns(tmp_path):
    trip_paths = {
        "branch-b": TripPath(
            trip_id="branch-b",
            route_id="A",
            direction_id=0,
            stops=(TripStop("a", 1), TripStop("b", 2), TripStop("c", 3)),
        ),
        "branch-d": TripPath(
            trip_id="branch-d",
            route_id="A",
            direction_id=0,
            stops=(TripStop("a", 1), TripStop("d", 2), TripStop("c", 3)),
        ),
    }
    topology = build_transit_topology(
        feed_label="branched-api-test",
        stops={
            stop_id: GTFSStop(stop_id, f"Stop {stop_id.upper()}")
            for stop_id in ("a", "b", "c", "d")
        },
        route_labels={"A": "Line A"},
        route_types={"A": 3},
        ride_edges=(),
        transfer_edges=(),
        trip_paths=trip_paths,
    )
    artifact = tmp_path / "branched-topology.json"
    save_transit_topology(topology, artifact)
    service = TransitAPIService(
        "redis://unused", store=_NoReadStore(), advisory_topology_path=artifact
    )

    payload = service.transit_alternative_advisory_options(
        disrupted_route_id="A", direction_id=0, requested_at_ms=NOW_MS
    )
    stops = {row["stop_id"]: row for row in payload["stops"]}

    assert payload["status"] == "available"
    assert stops["a"]["downstream_stop_ids"] == ["b", "d", "c"]
    assert stops["b"]["downstream_stop_ids"] == ["c"]
    assert stops["d"]["downstream_stop_ids"] == ["c"]
    assert stops["c"]["downstream_stop_ids"] == []


def test_advisory_options_do_not_offer_unselectable_directionless_pattern(tmp_path):
    topology = _topology()
    topology.trip_paths["a-directionless"] = TripPath(
        trip_id="a-directionless",
        route_id="A",
        direction_id=None,
        stops=(TripStop("origin", 1), TripStop("destination", 2)),
    )
    artifact = tmp_path / "mixed-directions.json"
    save_transit_topology(topology, artifact)
    service = TransitAPIService(
        "redis://unused", store=_NoReadStore(), advisory_topology_path=artifact
    )

    payload = service.transit_alternative_advisory_options(
        disrupted_route_id="A", requested_at_ms=NOW_MS
    )

    assert payload["status"] == "available"
    assert payload["resolved_direction_id"] == 0
    assert payload["directions"] == [{"direction_id": 0, "label": "Direction 0"}]


def test_advisory_options_are_unavailable_without_any_valid_stop_pair(tmp_path):
    topology = build_transit_topology(
        feed_label="singleton-patterns",
        stops={"a": GTFSStop("a", "Stop A"), "b": GTFSStop("b", "Stop B")},
        route_labels={"A": "Line A"},
        route_types={"A": 3},
        ride_edges=(),
        transfer_edges=(),
        trip_paths={
            "only-a": TripPath("only-a", "A", 0, (TripStop("a", 1),)),
            "only-b": TripPath("only-b", "A", 0, (TripStop("b", 1),)),
        },
    )
    artifact = tmp_path / "singleton-patterns.json"
    save_transit_topology(topology, artifact)
    service = TransitAPIService(
        "redis://unused", store=_NoReadStore(), advisory_topology_path=artifact
    )

    payload = service.transit_alternative_advisory_options(
        disrupted_route_id="A", direction_id=0, requested_at_ms=NOW_MS
    )

    assert payload["status"] == "unavailable"
    assert payload["stops"] == []
    assert payload["suppression_reasons"] == ["no_valid_stop_pair"]


def test_advisory_service_loads_topology_only_once(monkeypatch):
    calls = []

    def fake_load(path):
        calls.append(path)
        return _topology()

    monkeypatch.setattr("scripts.transit.api.load_transit_topology", fake_load)
    service = TransitAPIService(
        "redis://unused",
        store=_AdvisoryStore(),
        advisory_topology_path="compiled.json",
    )

    for _ in range(2):
        payload = service.transit_alternative_advisories(
            origin_stop_id="origin",
            destination_stop_id="destination",
            disrupted_route_id="A",
            requested_at_ms=NOW_MS,
        )
        assert payload["status"] == "published"

    assert calls == ["compiled.json"]


def test_concurrent_first_advisory_requests_wait_for_topology_load(monkeypatch):
    load_started = threading.Event()
    allow_load = threading.Event()
    second_started = threading.Event()
    second_done = threading.Event()
    payloads = []

    def blocking_load(_path):
        load_started.set()
        assert allow_load.wait(timeout=2)
        return _topology()

    monkeypatch.setattr("scripts.transit.api.load_transit_topology", blocking_load)
    service = TransitAPIService(
        "redis://unused",
        store=_NoReadStore(),
        advisory_topology_path="compiled.json",
    )

    def request_options(*, second=False):
        if second:
            second_started.set()
        payloads.append(
            service.transit_alternative_advisory_options(
                disrupted_route_id="A", requested_at_ms=NOW_MS
            )
        )
        if second:
            second_done.set()

    first = threading.Thread(target=request_options)
    second = threading.Thread(target=request_options, kwargs={"second": True})
    first.start()
    assert load_started.wait(timeout=1)
    second.start()
    assert second_started.wait(timeout=1)
    try:
        assert not second_done.wait(timeout=0.1)
    finally:
        allow_load.set()
        first.join(timeout=2)
        second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert [payload["status"] for payload in payloads] == ["available", "available"]


@pytest.mark.parametrize(
    ("configured_path", "expected_reason"),
    [(None, "topology_not_configured"), ("missing.json", "topology_not_found")],
)
def test_advisory_service_fails_closed_when_topology_unavailable(
    tmp_path, configured_path, expected_reason
):
    path = None if configured_path is None else tmp_path / configured_path
    service = TransitAPIService(
        "redis://unused",
        store=_NoReadStore(),
        advisory_topology_path=path,
    )

    payload = service.transit_alternative_advisories(
        origin_stop_id="origin",
        destination_stop_id="destination",
        disrupted_route_id="A",
        requested_at_ms=NOW_MS,
    )

    assert payload["status"] == "unavailable"
    assert payload["origin_stop_id"] == "origin"
    assert payload["destination_stop_id"] == "destination"
    assert payload["disrupted_route_id"] == "A"
    assert payload["suppression_reasons"] == [expected_reason]
    assert payload["advisories"] == []
    assert payload["release_stage"] == "operator_preview"


def test_advisory_service_requires_direction_when_topology_is_ambiguous():
    topology = _topology(ambiguous_disrupted_direction=True)

    with pytest.raises(AdvisoryRequestError, match="cannot infer") as exc_info:
        TransitAPIService._resolve_advisory_direction(
            topology,
            origin_stop_id="origin",
            destination_stop_id="destination",
            disrupted_route_id="A",
            direction_id=None,
        )

    assert exc_info.value.code == "ambiguous_direction"
    assert (
        TransitAPIService._resolve_advisory_direction(
            topology,
            origin_stop_id="origin",
            destination_stop_id="destination",
            disrupted_route_id="A",
            direction_id=1,
        )
        == 1
    )


def test_advisory_service_preserves_unambiguous_route_level_health():
    topology = _topology()
    route_level_path = TripPath(
        trip_id="route-level-trip",
        route_id="route-level",
        direction_id=None,
        stops=(TripStop("origin", 1), TripStop("destination", 2)),
    )
    topology = build_transit_topology(
        feed_label=topology.feed_label,
        stops=topology.stops,
        route_labels={**topology.route_labels, "route-level": "Route Level"},
        route_types={**topology.route_types, "route-level": 3},
        ride_edges=topology.ride_edges,
        transfer_edges=topology.transfer_edges,
        trip_paths={**topology.trip_paths, route_level_path.trip_id: route_level_path},
    )

    assert (
        TransitAPIService._resolve_advisory_direction(
            topology,
            origin_stop_id="origin",
            destination_stop_id="destination",
            disrupted_route_id="route-level",
            direction_id=None,
        )
        is None
    )
    health = TransitAPIService._directional_route_health(
        {
            "regimes": [
                {
                    "route_id": "route-level",
                    "regime": "service_degraded",
                    "hazard": 0.8,
                    "confidence": 0.9,
                    "timestamp_ms": NOW_MS,
                    "metrics": {},
                }
            ]
        }
    )
    assert health[("route-level", None)].direction_id is None


def test_advisory_service_rejects_invalid_topology_artifact(tmp_path):
    artifact = tmp_path / "invalid.json"
    artifact.write_text('{"schema_version":999}', encoding="utf-8")
    service = TransitAPIService(
        "redis://unused",
        store=_NoReadStore(),
        advisory_topology_path=artifact,
    )

    payload = service.transit_alternative_advisories(
        origin_stop_id="origin",
        destination_stop_id="destination",
        disrupted_route_id="A",
        requested_at_ms=NOW_MS,
    )

    assert payload["status"] == "unavailable"
    assert payload["suppression_reasons"] == ["topology_invalid"]


def test_advisory_cli_accepts_topology_path(monkeypatch):
    monkeypatch.setenv("TRANSIT_ADVISORY_TOPOLOGY_PATH", "/data/topology.json.gz")
    assert build_parser().parse_args([]).advisory_topology == "/data/topology.json.gz"
    assert (
        build_parser().parse_args(["--advisory-topology", "override.json"]).advisory_topology
        == "override.json"
    )


class _HTTPAdvisoryService:
    def __init__(self):
        self.calls = 0
        self.options_calls = 0

    def transit_alternative_advisories(self, **kwargs):
        self.calls += 1
        return {
            "status": "suppressed",
            "generated_at_ms": NOW_MS,
            "origin_stop_id": kwargs["origin_stop_id"],
            "destination_stop_id": kwargs["destination_stop_id"],
            "disrupted_route_id": kwargs["disrupted_route_id"],
            "advisories": [],
            "suppression_reasons": ["no_materially_better_reliable_alternative"],
            "evaluated_candidate_count": 1,
            "baseline_arrival_time_ms": NOW_MS + 900_000,
            "release_stage": "operator_preview",
            "resolved_direction_id": kwargs["direction_id"],
            "product_boundary": ADVISORY_PRODUCT_BOUNDARY,
        }

    def transit_alternative_advisory_options(self, **kwargs):
        self.options_calls += 1
        return {
            "status": "available",
            "generated_at_ms": NOW_MS,
            "disrupted_route_id": kwargs["disrupted_route_id"],
            "route_label": "Line A",
            "resolved_direction_id": kwargs["direction_id"],
            "directions": [{"direction_id": 0, "label": "Direction 0"}],
            "stops": [
                {
                    "stop_id": "origin",
                    "stop_name": "Origin",
                    "sequence": 1,
                    "downstream_stop_ids": ["destination"],
                },
                {
                    "stop_id": "destination",
                    "stop_name": "Destination",
                    "sequence": 2,
                    "downstream_stop_ids": [],
                },
            ],
            "suppression_reasons": [],
            "release_stage": "operator_preview",
            "product_boundary": ADVISORY_PRODUCT_BOUNDARY,
        }


def _get_json(url, *, token=None):
    request = Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_advisory_http_endpoint_fails_closed_without_registered_tokens():
    service = _HTTPAdvisoryService()
    with mock.patch.dict(
        os.environ,
        {"TRANSIT_API_TOKENS": "", "TRANSIT_API_REQUIRE_AUTH": ""},
        clear=False,
    ):
        reload_registry()
        server = start_transit_http_server(service, host="127.0.0.1", port=0)
        try:
            query = urlencode(
                {
                    "origin_stop_id": "origin",
                    "destination_stop_id": "destination",
                    "disrupted_route_id": "A",
                }
            )
            url = (
                f"http://127.0.0.1:{server.server_port}"
                f"/api/transit/alternative-advisories?{query}"
            )
            anonymous_status, anonymous_payload = _get_json(url)
            unknown_status, unknown_payload = _get_json(url, token="not-registered")
        finally:
            server.shutdown()
            server.server_close()
    reload_registry()

    assert anonymous_status == 401
    assert unknown_status == 401
    assert anonymous_payload["authentication_required"] is True
    assert unknown_payload["required_role"] == "operator"
    assert service.calls == 0


def test_advisory_http_endpoint_requires_registered_operator_and_validates_query():
    service = _HTTPAdvisoryService()
    with mock.patch.dict(
        os.environ,
        {
            "TRANSIT_API_TOKENS": "read:viewer,operate:operator,root:admin",
            "TRANSIT_API_REQUIRE_AUTH": "1",
        },
        clear=False,
    ):
        reload_registry()
        server = start_transit_http_server(service, host="127.0.0.1", port=0)
        try:
            base_url = (
                f"http://127.0.0.1:{server.server_port}"
                "/api/transit/alternative-advisories"
            )
            query = urlencode(
                {
                    "origin_stop_id": "origin",
                    "destination_stop_id": "destination",
                    "disrupted_route_id": "A",
                    "direction_id": 0,
                }
            )
            viewer_status, _ = _get_json(f"{base_url}?{query}", token="read")
            operator_status, operator_payload = _get_json(
                f"{base_url}?{query}", token="operate"
            )
            admin_status, admin_payload = _get_json(
                f"{base_url}?{query}", token="root"
            )
            missing_status, missing_payload = _get_json(
                f"{base_url}?origin_stop_id=origin", token="operate"
            )
            invalid_status, invalid_payload = _get_json(
                f"{base_url}?origin_stop_id=origin&destination_stop_id=destination"
                "&disrupted_route_id=A&direction_id=outbound",
                token="operate",
            )
        finally:
            server.shutdown()
            server.server_close()
    reload_registry()

    assert viewer_status == 401
    assert operator_status == 200
    assert admin_status == 200
    assert operator_payload["release_stage"] == "operator_preview"
    assert admin_payload["release_stage"] == "operator_preview"
    assert missing_status == 400
    assert missing_payload["error"] == "missing_query_parameter"
    assert missing_payload["missing_parameters"] == [
        "destination_stop_id",
        "disrupted_route_id",
    ]
    assert invalid_status == 400
    assert invalid_payload["error"] == "invalid_direction_id"
    assert invalid_payload["product_boundary"]["advisory_only"] is True
    assert service.calls == 2


def test_advisory_options_http_endpoint_is_operator_only_and_validates_query():
    service = _HTTPAdvisoryService()
    with mock.patch.dict(
        os.environ,
        {
            "TRANSIT_API_TOKENS": "read:viewer,operate:operator",
            "TRANSIT_API_REQUIRE_AUTH": "",
        },
        clear=False,
    ):
        reload_registry()
        server = start_transit_http_server(service, host="127.0.0.1", port=0)
        try:
            base_url = (
                f"http://127.0.0.1:{server.server_port}"
                "/api/transit/alternative-advisories/options"
            )
            query = urlencode({"disrupted_route_id": "A", "direction_id": 0})
            viewer_status, _ = _get_json(f"{base_url}?{query}", token="read")
            operator_status, operator_payload = _get_json(
                f"{base_url}?{query}", token="operate"
            )
            missing_status, missing_payload = _get_json(base_url, token="operate")
            invalid_status, invalid_payload = _get_json(
                f"{base_url}?disrupted_route_id=A&direction_id=outbound",
                token="operate",
            )
        finally:
            server.shutdown()
            server.server_close()
    reload_registry()

    assert viewer_status == 401
    assert operator_status == 200
    assert operator_payload["status"] == "available"
    assert operator_payload["stops"][0]["stop_id"] == "origin"
    assert missing_status == 400
    assert missing_payload["missing_parameters"] == ["disrupted_route_id"]
    assert invalid_status == 400
    assert invalid_payload["error"] == "invalid_direction_id"
    assert service.options_calls == 1


def test_operator_preview_http_responses_are_not_cacheable():
    service = _HTTPAdvisoryService()
    with mock.patch.dict(
        os.environ,
        {
            "TRANSIT_API_TOKENS": "operate:operator",
            "TRANSIT_API_REQUIRE_AUTH": "",
        },
        clear=False,
    ):
        reload_registry()
        server = start_transit_http_server(service, host="127.0.0.1", port=0)
        try:
            query = urlencode({"disrupted_route_id": "A", "direction_id": 0})
            request = Request(
                f"http://127.0.0.1:{server.server_port}"
                f"/api/transit/alternative-advisories/options?{query}",
                headers={"Authorization": "Bearer operate"},
            )
            with urlopen(request) as response:
                assert response.headers["Cache-Control"] == "no-store"
                assert response.headers.get("ETag") is None
        finally:
            server.shutdown()
            server.server_close()
    reload_registry()


def test_advisory_http_endpoint_returns_service_unavailable_when_disabled():
    service = TransitAPIService(
        "redis://unused", store=_NoReadStore(), advisory_topology_path=None
    )
    with mock.patch.dict(
        os.environ,
        {"TRANSIT_API_TOKENS": "operate:operator"},
        clear=False,
    ):
        reload_registry()
        server = start_transit_http_server(service, host="127.0.0.1", port=0)
        try:
            query = urlencode(
                {
                    "origin_stop_id": "origin",
                    "destination_stop_id": "destination",
                    "disrupted_route_id": "A",
                }
            )
            status, payload = _get_json(
                f"http://127.0.0.1:{server.server_port}"
                f"/api/transit/alternative-advisories?{query}",
                token="operate",
            )
        finally:
            server.shutdown()
            server.server_close()
    reload_registry()

    assert status == 503
    assert payload["status"] == "unavailable"
    assert payload["origin_stop_id"] == "origin"
    assert payload["destination_stop_id"] == "destination"
    assert payload["disrupted_route_id"] == "A"
    assert payload["suppression_reasons"] == ["topology_not_configured"]


def test_advisory_http_endpoint_rejects_ambiguous_direction(tmp_path):
    artifact = tmp_path / "ambiguous.json"
    save_transit_topology(_topology(ambiguous_disrupted_direction=True), artifact)
    service = TransitAPIService(
        "redis://unused",
        store=_NoReadStore(),
        advisory_topology_path=artifact,
    )
    with mock.patch.dict(
        os.environ,
        {"TRANSIT_API_TOKENS": "operate:operator"},
        clear=False,
    ):
        reload_registry()
        server = start_transit_http_server(service, host="127.0.0.1", port=0)
        try:
            query = urlencode(
                {
                    "origin_stop_id": "origin",
                    "destination_stop_id": "destination",
                    "disrupted_route_id": "A",
                }
            )
            status, payload = _get_json(
                f"http://127.0.0.1:{server.server_port}"
                f"/api/transit/alternative-advisories?{query}",
                token="operate",
            )
        finally:
            server.shutdown()
            server.server_close()
    reload_registry()

    assert status == 400
    assert payload["status"] == "invalid_request"
    assert payload["error"] == "ambiguous_direction"
    assert "direction_id is required" in payload["message"]
