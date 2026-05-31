#!/usr/bin/env python3
"""Capture and compare Transit Sentinel API behavior during API migrations.

The FastAPI migration path needs a contract gate before any traffic moves. This
tool records the current API handler behavior and compares a candidate server
against it by status code, JSON shape, ETag support, and conditional GET
behavior. Exact body and exact ETag comparison are opt-in because live MBTA
payloads contain timestamps and can change between requests.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.shared.runtime import isoformat_ms


@dataclasses.dataclass(frozen=True)
class EndpointCase:
    case_id: str
    method: str
    path: str
    requires_auth: bool = False
    body: dict[str, Any] | None = None


DEFAULT_CASES: tuple[EndpointCase, ...] = (
    EndpointCase("health", "GET", "/health"),
    EndpointCase("api_health", "GET", "/api/health"),
    EndpointCase("status_network", "GET", "/api/status/network"),
    EndpointCase("status_feed_quality", "GET", "/api/status/feed-quality"),
    EndpointCase("status_triage", "GET", "/api/status/triage?limit=12"),
    EndpointCase("status_routes", "GET", "/api/status/routes"),
    EndpointCase("status_alerts", "GET", "/api/status/alerts"),
    EndpointCase("status_scorecard", "GET", "/api/status/scorecard?limit=60"),
    EndpointCase(
        "transit_dashboard",
        "GET",
        "/api/transit/dashboard?scope=live",
        requires_auth=True,
    ),
    EndpointCase(
        "transit_health", "GET", "/api/transit/health?scope=live", requires_auth=True
    ),
    EndpointCase(
        "transit_entities",
        "GET",
        "/api/transit/entities?scope=live",
        requires_auth=True,
    ),
    EndpointCase(
        "transit_regimes",
        "GET",
        "/api/transit/regimes?scope=live",
        requires_auth=True,
    ),
    EndpointCase(
        "transit_incidents",
        "GET",
        "/api/transit/incidents?scope=live",
        requires_auth=True,
    ),
    EndpointCase(
        "transit_trends", "GET", "/api/transit/trends?scope=live", requires_auth=True
    ),
    EndpointCase("transit_sources", "GET", "/api/transit/sources", requires_auth=True),
    EndpointCase("transit_map", "GET", "/api/transit/map?scope=live", requires_auth=True),
    EndpointCase(
        "transit_scorecard",
        "GET",
        "/api/transit/scorecard?scope=live&limit=60",
        requires_auth=True,
    ),
)

HEADER_ALLOWLIST = {
    "cache-control",
    "content-type",
    "etag",
    "access-control-allow-origin",
    "access-control-expose-headers",
}


def default_cases(
    *,
    history_entity_id: str = "",
    include_admin: bool = False,
) -> list[EndpointCase]:
    cases = list(DEFAULT_CASES)
    if history_entity_id:
        cases.append(
            EndpointCase(
                "transit_history",
                "GET",
                (
                    "/api/transit/history?scope=live&limit=36&entity_id="
                    + quote(history_entity_id, safe="")
                ),
                requires_auth=True,
            )
        )
    if include_admin:
        cases.append(
            EndpointCase(
                "transit_audit",
                "GET",
                "/api/transit/audit?limit=20",
                requires_auth=True,
            )
        )
    return cases


def fetch_case(
    base_url: str,
    case: EndpointCase,
    *,
    bearer_token: str = "",
    timeout: float = 10.0,
) -> dict[str, Any]:
    response = _fetch_once(
        base_url,
        case,
        bearer_token=bearer_token,
        timeout=timeout,
        extra_headers={},
    )
    conditional: dict[str, Any] | None = None
    etag = str(response["headers"].get("etag") or "")
    if case.method.upper() == "GET" and response["status_code"] == 200 and etag:
        conditional = _fetch_once(
            base_url,
            case,
            bearer_token=bearer_token,
            timeout=timeout,
            extra_headers={"If-None-Match": etag},
            include_json=False,
        )
    return {
        "case_id": case.case_id,
        "request": {
            "method": case.method.upper(),
            "path": case.path,
            "requires_auth": case.requires_auth,
        },
        "response": response,
        "conditional_get": conditional,
    }


def _fetch_once(
    base_url: str,
    case: EndpointCase,
    *,
    bearer_token: str,
    timeout: float,
    extra_headers: dict[str, str],
    include_json: bool = True,
) -> dict[str, Any]:
    method = case.method.upper()
    data = None
    headers = {"Accept": "application/json", **extra_headers}
    if case.requires_auth and bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    if case.body is not None:
        data = json.dumps(case.body, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        headers["Content-Type"] = "application/json"
    request = Request(
        _join_url(base_url, case.path),
        data=data,
        headers=headers,
        method=method,
    )
    started = time.monotonic()
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
            status_code = int(response.status)
            response_headers = _headers(response.headers)
    except HTTPError as exc:
        body = exc.read()
        status_code = int(exc.code)
        response_headers = _headers(exc.headers)
    latency_ms = round((time.monotonic() - started) * 1000.0, 1)
    parsed_json, json_error = _parse_json(body)
    payload: dict[str, Any] = {
        "status_code": status_code,
        "headers": response_headers,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_bytes": len(body),
        "latency_ms": latency_ms,
    }
    if include_json and json_error is None:
        payload["json"] = parsed_json
        payload["json_shape"] = json_shape(parsed_json)
    elif include_json and json_error:
        payload["json_error"] = json_error
    return payload


def _join_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/") + "/"
    return urljoin(base, path.lstrip("/"))


def _headers(headers: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        lowered = str(key).lower()
        if lowered in HEADER_ALLOWLIST:
            out[lowered] = str(value)
    return out


def _parse_json(body: bytes) -> tuple[Any, str | None]:
    if not body:
        return None, "empty_body"
    try:
        return json.loads(body.decode("utf-8")), None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, str(exc)


def json_shape(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        return {
            "type": "array",
            "items": merge_shapes([json_shape(item) for item in value]),
        }
    if isinstance(value, dict):
        return {
            "type": "object",
            "fields": {
                str(key): json_shape(item) for key, item in sorted(value.items())
            },
        }
    return {"type": type(value).__name__}


def merge_shapes(shapes: list[dict[str, Any]]) -> dict[str, Any]:
    if not shapes:
        return {"type": "empty"}
    type_names = {shape.get("type") for shape in shapes}
    if type_names == {"object"}:
        fields: dict[str, list[dict[str, Any]]] = {}
        present_counts: dict[str, int] = {}
        for shape in shapes:
            shape_fields = shape.get("fields") or {}
            for key, value in shape_fields.items():
                fields.setdefault(key, []).append(value)
                present_counts[key] = present_counts.get(key, 0) + 1
        return {
            "type": "object",
            "fields": {
                key: {
                    **merge_shapes(values),
                    **({"optional": True} if present_counts[key] < len(shapes) else {}),
                }
                for key, values in sorted(fields.items())
            },
        }
    if type_names == {"array"}:
        return {
            "type": "array",
            "items": merge_shapes([shape["items"] for shape in shapes]),
        }
    unique = _unique_shapes(shapes)
    if len(unique) == 1:
        return unique[0]
    return {"type": "union", "any_of": unique}


def _unique_shapes(shapes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keyed = {json.dumps(shape, sort_keys=True, separators=(",", ":")): shape for shape in shapes}
    return [keyed[key] for key in sorted(keyed)]


def compare_records(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    strict_body: bool = False,
    strict_etag: bool = False,
) -> list[str]:
    case_id = str(baseline.get("case_id") or candidate.get("case_id") or "unknown")
    diffs: list[str] = []
    b_response = baseline.get("response") or {}
    c_response = candidate.get("response") or {}
    if b_response.get("status_code") != c_response.get("status_code"):
        diffs.append(
            f"{case_id}: status {b_response.get('status_code')} != {c_response.get('status_code')}"
        )
    b_json = b_response.get("json")
    c_json = c_response.get("json")
    if b_json is not None and c_json is not None:
        diffs.extend(
            f"{case_id}: {diff}"
            for diff in shape_diffs(json_shape(b_json), json_shape(c_json))
        )
    elif (b_json is None) != (c_json is None):
        diffs.append(f"{case_id}: JSON body presence differs")
    b_headers = b_response.get("headers") or {}
    c_headers = c_response.get("headers") or {}
    b_etag = b_headers.get("etag")
    c_etag = c_headers.get("etag")
    if bool(b_etag) != bool(c_etag):
        diffs.append(f"{case_id}: ETag presence differs")
    if strict_etag and b_etag != c_etag:
        diffs.append(f"{case_id}: ETag value differs")
    if strict_body and b_response.get("body_sha256") != c_response.get("body_sha256"):
        diffs.append(f"{case_id}: body hash differs")
    diffs.extend(_conditional_diffs(case_id, baseline, candidate, strict_etag=strict_etag))
    return diffs


def shape_diffs(
    baseline: dict[str, Any], candidate: dict[str, Any], path: str = "$"
) -> list[str]:
    diffs: list[str] = []
    b_type = baseline.get("type")
    c_type = candidate.get("type")
    if b_type != c_type:
        return [f"{path}: type {b_type} != {c_type}"]
    if b_type == "object":
        b_fields = baseline.get("fields") or {}
        c_fields = candidate.get("fields") or {}
        b_keys = set(b_fields)
        c_keys = set(c_fields)
        for key in sorted(b_keys - c_keys):
            diffs.append(f"{path}.{key}: missing in candidate")
        for key in sorted(c_keys - b_keys):
            diffs.append(f"{path}.{key}: extra in candidate")
        for key in sorted(b_keys & c_keys):
            diffs.extend(shape_diffs(b_fields[key], c_fields[key], f"{path}.{key}"))
    elif b_type == "array":
        diffs.extend(
            shape_diffs(
                baseline.get("items") or {"type": "empty"},
                candidate.get("items") or {"type": "empty"},
                f"{path}[]",
            )
        )
    elif b_type == "union":
        if baseline != candidate:
            diffs.append(f"{path}: union shape differs")
    return diffs


def _conditional_diffs(
    case_id: str,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    strict_etag: bool,
) -> list[str]:
    diffs: list[str] = []
    b_cond = baseline.get("conditional_get")
    c_cond = candidate.get("conditional_get")
    if bool(b_cond) != bool(c_cond):
        return [f"{case_id}: conditional GET coverage differs"]
    if not b_cond or not c_cond:
        return diffs
    if b_cond.get("status_code") != c_cond.get("status_code"):
        diffs.append(
            f"{case_id}: conditional status {b_cond.get('status_code')} != {c_cond.get('status_code')}"
        )
    if b_cond.get("body_bytes") != c_cond.get("body_bytes"):
        diffs.append(f"{case_id}: conditional body byte count differs")
    if strict_etag and (b_cond.get("headers") or {}).get("etag") != (
        c_cond.get("headers") or {}
    ).get("etag"):
        diffs.append(f"{case_id}: conditional ETag value differs")
    return diffs


def capture(
    *,
    base_url: str,
    output_dir: Path,
    bearer_token: str,
    history_entity_id: str,
    include_admin: bool,
    include_ops_without_token: bool,
    timeout: float,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cases_dir = output_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    records = []
    skipped = []
    for case in default_cases(
        history_entity_id=history_entity_id,
        include_admin=include_admin,
    ):
        if case.requires_auth and not bearer_token and not include_ops_without_token:
            skipped.append({"case_id": case.case_id, "reason": "missing_bearer_token"})
            continue
        record = fetch_case(
            base_url,
            case,
            bearer_token=bearer_token,
            timeout=timeout,
        )
        _write_json(cases_dir / f"{case.case_id}.json", record)
        records.append(_case_summary(record))
    manifest = {
        "generated_at": isoformat_ms(),
        "base_url": base_url,
        "case_count": len(records),
        "skipped_count": len(skipped),
        "cases": records,
        "skipped": skipped,
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def compare_urls(
    *,
    baseline_url: str,
    candidate_url: str,
    bearer_token: str,
    baseline_token: str,
    candidate_token: str,
    history_entity_id: str,
    include_admin: bool,
    include_ops_without_token: bool,
    timeout: float,
    strict_body: bool,
    strict_etag: bool,
) -> dict[str, Any]:
    diffs: list[str] = []
    cases = []
    b_token = baseline_token or bearer_token
    c_token = candidate_token or bearer_token
    for case in default_cases(
        history_entity_id=history_entity_id,
        include_admin=include_admin,
    ):
        if case.requires_auth and not (b_token and c_token) and not include_ops_without_token:
            cases.append(
                {
                    "case_id": case.case_id,
                    "status": "skipped",
                    "reason": "missing_bearer_token",
                }
            )
            continue
        baseline = fetch_case(
            baseline_url,
            case,
            bearer_token=b_token,
            timeout=timeout,
        )
        candidate = fetch_case(
            candidate_url,
            case,
            bearer_token=c_token,
            timeout=timeout,
        )
        case_diffs = compare_records(
            baseline,
            candidate,
            strict_body=strict_body,
            strict_etag=strict_etag,
        )
        diffs.extend(case_diffs)
        cases.append(
            {
                "case_id": case.case_id,
                "status": "failed" if case_diffs else "passed",
                "diff_count": len(case_diffs),
                "baseline": _case_summary(baseline),
                "candidate": _case_summary(candidate),
            }
        )
    return {
        "generated_at": isoformat_ms(),
        "baseline_url": baseline_url,
        "candidate_url": candidate_url,
        "case_count": len(cases),
        "diff_count": len(diffs),
        "status": "failed" if diffs else "passed",
        "diffs": diffs,
        "cases": cases,
    }


def verify_fixtures(
    *,
    base_url: str,
    fixture_dir: Path,
    bearer_token: str,
    timeout: float,
    strict_body: bool,
    strict_etag: bool,
) -> dict[str, Any]:
    cases_dir = fixture_dir / "cases"
    diffs: list[str] = []
    cases = []
    for fixture_path in sorted(cases_dir.glob("*.json")):
        fixture = _read_json(fixture_path)
        request = fixture.get("request") or {}
        case = EndpointCase(
            str(fixture.get("case_id") or fixture_path.stem),
            str(request.get("method") or "GET"),
            str(request.get("path") or ""),
            bool(request.get("requires_auth")),
        )
        current = fetch_case(
            base_url,
            case,
            bearer_token=bearer_token,
            timeout=timeout,
        )
        case_diffs = compare_records(
            fixture,
            current,
            strict_body=strict_body,
            strict_etag=strict_etag,
        )
        diffs.extend(case_diffs)
        cases.append(
            {
                "case_id": case.case_id,
                "status": "failed" if case_diffs else "passed",
                "diff_count": len(case_diffs),
                "current": _case_summary(current),
            }
        )
    return {
        "generated_at": isoformat_ms(),
        "base_url": base_url,
        "fixture_dir": str(fixture_dir),
        "case_count": len(cases),
        "diff_count": len(diffs),
        "status": "failed" if diffs else "passed",
        "diffs": diffs,
        "cases": cases,
    }


def _case_summary(record: dict[str, Any]) -> dict[str, Any]:
    response = record.get("response") or {}
    conditional = record.get("conditional_get") or {}
    return {
        "case_id": record.get("case_id"),
        "status_code": response.get("status_code"),
        "body_bytes": response.get("body_bytes"),
        "etag": bool((response.get("headers") or {}).get("etag")),
        "conditional_status_code": conditional.get("status_code"),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _print_report(report: dict[str, Any], output: str) -> None:
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture or compare Transit Sentinel API parity fixtures."
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--bearer-token",
        default=os.getenv("TRANSIT_API_PARITY_BEARER_TOKEN", ""),
        help="Bearer token used for protected /api/transit/* reads.",
    )
    common.add_argument(
        "--history-entity-id",
        default="",
        help="Optional entity id to include /api/transit/history in parity cases.",
    )
    common.add_argument(
        "--include-admin",
        action="store_true",
        help="Include admin-only audit reads. Requires an admin bearer token.",
    )
    common.add_argument(
        "--include-ops-without-token",
        action="store_true",
        help="Probe protected ops paths without a token instead of skipping them.",
    )
    common.add_argument("--timeout", type=float, default=10.0)
    common.add_argument("--strict-body", action="store_true")
    common.add_argument("--strict-etag", action="store_true")
    common.add_argument("--output", default="")
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture_parser = subparsers.add_parser(
        "capture", parents=[common], help="Capture fixtures."
    )
    capture_parser.add_argument("--base-url", required=True)
    capture_parser.add_argument(
        "--output-dir",
        default="output/api-parity/current",
        help="Directory that receives manifest.json and per-case fixtures.",
    )

    compare_parser = subparsers.add_parser(
        "compare", parents=[common], help="Compare two live APIs."
    )
    compare_parser.add_argument("--baseline-url", required=True)
    compare_parser.add_argument("--candidate-url", required=True)
    compare_parser.add_argument(
        "--baseline-token",
        default=os.getenv("TRANSIT_API_PARITY_BASELINE_TOKEN", ""),
    )
    compare_parser.add_argument(
        "--candidate-token",
        default=os.getenv("TRANSIT_API_PARITY_CANDIDATE_TOKEN", ""),
    )

    verify_parser = subparsers.add_parser(
        "verify-fixtures",
        parents=[common],
        help="Compare a live API against captured fixtures.",
    )
    verify_parser.add_argument("--base-url", required=True)
    verify_parser.add_argument("--fixture-dir", required=True)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "capture":
        report = capture(
            base_url=str(args.base_url),
            output_dir=Path(args.output_dir),
            bearer_token=str(args.bearer_token or ""),
            history_entity_id=str(args.history_entity_id or ""),
            include_admin=bool(args.include_admin),
            include_ops_without_token=bool(args.include_ops_without_token),
            timeout=float(args.timeout),
        )
    elif args.command == "compare":
        report = compare_urls(
            baseline_url=str(args.baseline_url),
            candidate_url=str(args.candidate_url),
            bearer_token=str(args.bearer_token or ""),
            baseline_token=str(args.baseline_token or ""),
            candidate_token=str(args.candidate_token or ""),
            history_entity_id=str(args.history_entity_id or ""),
            include_admin=bool(args.include_admin),
            include_ops_without_token=bool(args.include_ops_without_token),
            timeout=float(args.timeout),
            strict_body=bool(args.strict_body),
            strict_etag=bool(args.strict_etag),
        )
    elif args.command == "verify-fixtures":
        report = verify_fixtures(
            base_url=str(args.base_url),
            fixture_dir=Path(args.fixture_dir),
            bearer_token=str(args.bearer_token or ""),
            timeout=float(args.timeout),
            strict_body=bool(args.strict_body),
            strict_etag=bool(args.strict_etag),
        )
    else:  # pragma: no cover - argparse prevents this
        raise AssertionError(f"unknown command {args.command}")
    _print_report(report, str(args.output or ""))
    return 1 if report.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
