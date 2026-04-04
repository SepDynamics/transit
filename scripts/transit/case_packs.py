"""Case-pack metadata and event overlay helpers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional


CASE_PACK_METADATA_FILENAME = "case_pack.json"
EVENT_OVERLAYS_FILENAME = "event_overlays.json"


def resolve_case_pack_root(path: str | Path) -> Optional[Path]:
    candidate = Path(path).resolve()
    for current in [candidate, *candidate.parents]:
        if current.is_file():
            continue
        if (current / CASE_PACK_METADATA_FILENAME).exists():
            return current
        if (current / "labels").is_dir() and any((current / "labels").rglob("*.json")):
            return current
    return None


def load_case_pack_metadata(case_pack_root: str | Path) -> Dict[str, Any]:
    root = Path(case_pack_root).resolve()
    metadata_path = root / CASE_PACK_METADATA_FILENAME
    payload: Dict[str, Any] = {}
    if metadata_path.exists():
        try:
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = dict(loaded)
        except (OSError, json.JSONDecodeError):
            payload = {}
    city_key = str(payload.get("city_key") or root.parent.name or "").strip() or None
    city_name = str(payload.get("city_name") or city_key or "").strip() or None
    event_key = str(payload.get("event_key") or "").strip() or None
    event_name = str(payload.get("event_name") or "").strip() or None
    category = str(payload.get("category") or "").strip() or None
    case_pack_id = str(payload.get("case_pack_id") or root.name).strip()
    agency_keys = payload.get("agency_keys")
    if not isinstance(agency_keys, list):
        agency_keys = []
    return {
        **payload,
        "case_pack_id": case_pack_id,
        "city_key": city_key,
        "city_name": city_name,
        "event_key": event_key,
        "event_name": event_name,
        "category": category,
        "agency_keys": [str(value).strip() for value in agency_keys if str(value).strip()],
        "case_pack_root": str(root),
    }


def resolve_case_pack_event_overlay_path(case_pack_root: str | Path) -> Optional[Path]:
    root = Path(case_pack_root).resolve()
    metadata = load_case_pack_metadata(root)
    overlay_path = str(metadata.get("overlay_path") or "").strip()
    if overlay_path:
        candidate = Path(overlay_path)
        if not candidate.is_absolute():
            candidate = root / overlay_path
        if candidate.exists():
            return candidate.resolve()
    fallback = root / EVENT_OVERLAYS_FILENAME
    return fallback.resolve() if fallback.exists() else None


def load_event_overlays(path: str | Path | None) -> List[Dict[str, Any]]:
    if path in (None, ""):
        return []
    overlay_path = Path(path).resolve()
    try:
        payload = json.loads(overlay_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("overlays") if isinstance(payload, Mapping) else payload
    if not isinstance(rows, list):
        return []
    overlays: List[Dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            continue
        overlay = {
            "overlay_id": str(row.get("overlay_id") or f"overlay-{index:03d}"),
            "label": str(row.get("label") or row.get("event_name") or row.get("event_key") or "").strip(),
            "event_key": str(row.get("event_key") or "").strip() or None,
            "event_name": str(row.get("event_name") or "").strip() or None,
            "city_key": str(row.get("city_key") or "").strip() or None,
            "category": str(row.get("category") or "").strip() or None,
            "agency_keys": [str(value).strip() for value in (row.get("agency_keys") or []) if str(value).strip()],
            "route_ids": [str(value).strip() for value in (row.get("route_ids") or []) if str(value).strip()],
            "corridor_ids": [str(value).strip() for value in (row.get("corridor_ids") or []) if str(value).strip()],
            "starts_at": str(row.get("starts_at") or "").strip() or None,
            "ends_at": str(row.get("ends_at") or "").strip() or None,
            "note": str(row.get("note") or "").strip() or None,
            "source": str(row.get("source") or "").strip() or None,
        }
        overlays.append(overlay)
    return overlays


def overlay_matches_corridor(
    overlay: Mapping[str, Any],
    *,
    route_id: str | None,
    corridor_id: str | None,
    agency_key: str | None,
) -> bool:
    overlay_corridor_ids = {str(value).strip() for value in (overlay.get("corridor_ids") or []) if str(value).strip()}
    if overlay_corridor_ids and str(corridor_id or "").strip() in overlay_corridor_ids:
        return True
    overlay_route_ids = {str(value).strip() for value in (overlay.get("route_ids") or []) if str(value).strip()}
    if overlay_route_ids and str(route_id or "").strip() not in overlay_route_ids:
        return False
    overlay_agency_keys = {str(value).strip() for value in (overlay.get("agency_keys") or []) if str(value).strip()}
    if overlay_agency_keys and str(agency_key or "").strip() not in overlay_agency_keys:
        return False
    if overlay_route_ids or overlay_corridor_ids:
        return True
    return bool(overlay_agency_keys)


def summarize_matching_overlays(
    overlays: List[Dict[str, Any]],
    *,
    route_id: str | None,
    corridor_id: str | None,
    agency_key: str | None,
) -> List[Dict[str, Any]]:
    return [
        overlay
        for overlay in overlays
        if overlay_matches_corridor(overlay, route_id=route_id, corridor_id=corridor_id, agency_key=agency_key)
    ]
