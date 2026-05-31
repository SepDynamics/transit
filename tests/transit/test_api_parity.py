from pathlib import Path

from scripts.transit.api import start_transit_http_server
from scripts.transit.api_parity import (
    EndpointCase,
    capture,
    compare_records,
    fetch_case,
    json_shape,
    verify_fixtures,
)


class _ParityService:
    def service_health(self):
        return {"service": "Transit Sentinel API", "status": "ok"}

    def public_status_network(self, *, scope="live", trace_id=None):
        return {
            "scope": scope,
            "trace_id": trace_id,
            "severity": "good",
            "active_route_count": 1,
            "disrupted_routes": [],
        }

    def public_status_routes(self, *, scope="live", trace_id=None):
        return {
            "scope": scope,
            "trace_id": trace_id,
            "route_count": 1,
            "routes": [{"entity_id": "route:Red:0", "severity": "good"}],
        }

    def public_status_feed_quality(self, *, scope="live", trace_id=None):
        return {
            "scope": scope,
            "trace_id": trace_id,
            "status": "good",
            "checks": [],
            "feed_status": {"status": "ok"},
        }

    def public_status_triage(self, *, scope="live", trace_id=None, limit=12):
        return {
            "scope": scope,
            "trace_id": trace_id,
            "triage_count": 0,
            "routes": [],
        }

    def public_status_alerts(self, *, scope="live", trace_id=None):
        return {"scope": scope, "trace_id": trace_id, "alert_count": 0, "alerts": []}

    def public_status_scorecard(self, *, scope="live", trace_id=None, limit=720):
        return {
            "scope": scope,
            "trace_id": trace_id,
            "window_snapshots": limit,
            "corridors": [],
        }


def test_json_shape_merges_array_object_fields() -> None:
    shape = json_shape(
        {
            "routes": [
                {"entity_id": "route:Red:0", "delay": 12},
                {"entity_id": "route:Green-B:0", "severity": "good"},
            ]
        }
    )

    item_fields = shape["fields"]["routes"]["items"]["fields"]
    assert item_fields["entity_id"]["type"] == "string"
    assert item_fields["delay"]["type"] == "number"
    assert item_fields["delay"]["optional"] is True
    assert item_fields["severity"]["type"] == "string"
    assert item_fields["severity"]["optional"] is True


def test_compare_records_accepts_value_changes_with_same_shape() -> None:
    baseline = {
        "case_id": "status",
        "response": {
            "status_code": 200,
            "headers": {"etag": '"a"'},
            "body_sha256": "a",
            "json": {"status": "ok", "routes": [{"delay": 12}]},
        },
        "conditional_get": {
            "status_code": 304,
            "headers": {"etag": '"a"'},
            "body_bytes": 0,
        },
    }
    candidate = {
        "case_id": "status",
        "response": {
            "status_code": 200,
            "headers": {"etag": '"b"'},
            "body_sha256": "b",
            "json": {"status": "disruption", "routes": [{"delay": 30}]},
        },
        "conditional_get": {
            "status_code": 304,
            "headers": {"etag": '"b"'},
            "body_bytes": 0,
        },
    }

    assert compare_records(baseline, candidate) == []
    assert compare_records(baseline, candidate, strict_body=True)
    assert compare_records(baseline, candidate, strict_etag=True)


def test_compare_records_reports_json_shape_drift() -> None:
    baseline = {
        "case_id": "status",
        "response": {
            "status_code": 200,
            "headers": {},
            "json": {"status": "ok", "route_count": 1},
        },
    }
    candidate = {
        "case_id": "status",
        "response": {
            "status_code": 200,
            "headers": {},
            "json": {"status": {"value": "ok"}, "route_count": 1},
        },
    }

    diffs = compare_records(baseline, candidate)

    assert diffs == ["status: $.status: type string != object"]


def test_fetch_case_records_etag_and_conditional_get() -> None:
    server = start_transit_http_server(_ParityService(), host="127.0.0.1", port=0)
    try:
        record = fetch_case(
            f"http://127.0.0.1:{server.server_port}",
            EndpointCase("health", "GET", "/health"),
        )
    finally:
        server.shutdown()
        server.server_close()

    assert record["response"]["status_code"] == 200
    assert record["response"]["headers"]["etag"]
    assert record["conditional_get"]["status_code"] == 304
    assert record["conditional_get"]["body_bytes"] == 0


def test_capture_and_verify_fixture_shape(tmp_path: Path) -> None:
    server = start_transit_http_server(_ParityService(), host="127.0.0.1", port=0)
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        manifest = capture(
            base_url=base_url,
            output_dir=tmp_path,
            bearer_token="",
            history_entity_id="",
            include_admin=False,
            include_ops_without_token=False,
            timeout=5.0,
        )
        report = verify_fixtures(
            base_url=base_url,
            fixture_dir=tmp_path,
            bearer_token="",
            timeout=5.0,
            strict_body=False,
            strict_etag=False,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert manifest["case_count"] == 8
    assert manifest["skipped_count"] == 9
    assert report["status"] == "passed"
    assert report["case_count"] == 8
