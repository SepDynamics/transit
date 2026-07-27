from pathlib import Path

import yaml


OPENAPI_PATH = (
    Path(__file__).resolve().parents[2]
    / "apps"
    / "frontend"
    / "public"
    / "static"
    / "transit.openapi.yaml"
)

PUBLIC_PATHS = {
    "/health",
    "/api/health",
    "/api/status",
    "/api/status/network",
    "/api/status/feed-quality",
    "/api/status/triage",
    "/api/status/routes",
    "/api/status/alerts",
    "/api/status/scorecard",
}

FRONTEND_OPS_PATHS = {
    "/api/transit/dashboard",
    "/api/transit/health",
    "/api/transit/entities",
    "/api/transit/regimes",
    "/api/transit/incidents",
    "/api/transit/trends",
    "/api/transit/history",
    "/api/transit/sources",
    "/api/transit/map",
    "/api/transit/scorecard",
}

ADMIN_OPS_PATHS = {
    "/api/transit/audit",
    "/api/transit/incidents/ack",
}

OPERATOR_PREVIEW_PATHS = {
    "/api/transit/alternative-advisories",
}


def test_openapi_documents_public_and_frontend_consumed_paths():
    spec = _load_spec()
    paths = set(spec["paths"])

    assert PUBLIC_PATHS <= paths
    assert FRONTEND_OPS_PATHS <= paths
    assert ADMIN_OPS_PATHS <= paths
    assert OPERATOR_PREVIEW_PATHS <= paths
    assert "TransitDashboardResponse" in spec["components"]["schemas"]
    assert "AlternativeAdvisoryDecision" in spec["components"]["schemas"]


def test_openapi_marks_ops_paths_as_bearer_auth_and_public_status_open():
    spec = _load_spec()

    for path in FRONTEND_OPS_PATHS | ADMIN_OPS_PATHS | OPERATOR_PREVIEW_PATHS:
        operation = _first_operation(spec["paths"][path])
        assert operation.get("security") == [{"bearerAuth": []}]

    for path in PUBLIC_PATHS:
        operation = _first_operation(spec["paths"][path])
        assert "security" not in operation


def test_openapi_documents_operator_preview_boundary_and_required_inputs():
    spec = _load_spec()
    operation = spec["paths"]["/api/transit/alternative-advisories"]["get"]
    assert "always requires" in operation["description"]
    assert "always fails closed" in operation["responses"]["401"]["description"]
    parameters = [
        spec["components"]["parameters"][row["$ref"].rsplit("/", 1)[-1]]
        for row in operation["parameters"]
    ]

    assert [parameter["name"] for parameter in parameters] == [
        "origin_stop_id",
        "destination_stop_id",
        "disrupted_route_id",
        "direction_id",
    ]
    assert [parameter.get("required", False) for parameter in parameters] == [
        True,
        True,
        True,
        False,
    ]
    assert set(operation["responses"]) == {"200", "400", "401", "503"}
    boundary = spec["components"]["schemas"]["AdvisoryProductBoundary"]
    assert boundary["additionalProperties"] is False
    assert boundary["properties"]["infers_cause"]["enum"] == [False]
    assert boundary["properties"]["guarantees_arrival"]["enum"] == [False]
    assert boundary["properties"]["issues_dispatch_instructions"]["enum"] == [
        False
    ]
    schemas = spec["components"]["schemas"]
    assert schemas["AdvisoryLeg"]["properties"]["kind"]["enum"] == [
        "ride",
        "walk",
        "transfer",
    ]
    assert "total_transfer_seconds" in schemas["AlternativeAdvisory"]["required"]
    assert schemas["AlternativeAdvisory"]["properties"]["total_transfer_seconds"] == {
        "type": "integer",
        "minimum": 0,
    }


def test_openapi_public_status_contract_is_closed_and_conditional():
    spec = _load_spec()
    schemas = spec["components"]["schemas"]
    public_schemas = {
        "FeedStatus",
        "RouteStatus",
        "DisruptedRoute",
        "PublicStatusRoutesResponse",
        "PublicStatusNetworkResponse",
        "PublicFeedQualityCheck",
        "PublicStatusFeedQualityResponse",
        "PublicTriageRoute",
        "PublicStatusTriageResponse",
        "PublicStatusAlert",
        "PublicStatusAlertsResponse",
        "PublicScorecardNetwork",
        "PublicScorecardCorridor",
        "PublicStatusScorecardResponse",
    }

    for name in public_schemas:
        assert schemas[name]["additionalProperties"] is False

    assert schemas["PublicStatusNetworkResponse"]["required"] == [
        "generated_at",
        "scope",
        "severity",
        "severity_label",
        "severity_color",
        "active_route_count",
        "incident_count",
        "critical_incident_count",
        "disrupted_route_count",
        "disrupted_routes",
    ]
    assert schemas["PublicStatusScorecardResponse"]["required"] == [
        "generated_at",
        "scope",
        "window_snapshots",
        "corridor_count",
        "total_incidents",
        "network",
        "corridors",
    ]

    for path in (
        "/api/status/network",
        "/api/status/feed-quality",
        "/api/status/triage",
        "/api/status/routes",
        "/api/status/alerts",
        "/api/status/scorecard",
    ):
        operation = spec["paths"][path]["get"]
        assert operation["parameters"][0]["$ref"].endswith("/PublicStatusScope")
        assert "ETag" in operation["responses"]["200"]["headers"]
        assert "304" in operation["responses"]

    scorecard_params = spec["paths"]["/api/status/scorecard"]["get"]["parameters"]
    assert scorecard_params[2]["$ref"].endswith("/PublicScorecardLimit")
    triage_params = spec["paths"]["/api/status/triage"]["get"]["parameters"]
    assert triage_params[2]["$ref"].endswith("/PublicTriageLimit")


def _load_spec():
    with OPENAPI_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _first_operation(path_item):
    for method in ("get", "post", "put", "patch", "delete"):
        if method in path_item:
            return path_item[method]
    raise AssertionError("path item has no operation")
