#!/usr/bin/env python3
"""Catalog LA discovery bundles and rank candidate evidence windows."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scripts.transit.feeds import load_gtfs_catalog, load_gtfs_realtime_resource

IMPACT_EFFECTS = {"SIGNIFICANT_DELAYS", "DETOUR", "NO_SERVICE", "REDUCED_SERVICE", "MODIFIED_SERVICE"}


def build_index(incoming: Path) -> dict[str, Any]:
    snapshots: list[dict[str, Any]] = []
    catalog_cache: dict[str, Any] = {}
    for bundle_path in sorted(incoming.glob("*/*/*/*.tar.gz")):
        with tarfile.open(bundle_path, "r:gz") as archive:
            members = {member.name: member for member in archive.getmembers() if member.isfile()}
            for name, member in members.items():
                if not name.endswith("/manifest.json"):
                    continue
                manifest = json.load(archive.extractfile(member))
                prefix = name.rsplit("/", 1)[0]
                snapshots.append(
                    _summarize_snapshot(archive, members, prefix, manifest, bundle_path, incoming, catalog_cache)
                )
    snapshots.sort(key=lambda row: row["timestamp_ms"])
    for row in snapshots:
        row["window"] = _window(row["timestamp_ms"])
    candidates = [row for row in snapshots if row["candidate_score"] > 0]
    return {
        "schema_version": "sentinel.lametro_candidate_index.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bundle_count": len(list(incoming.glob("*/*/*/*.tar.gz"))),
        "snapshot_count": len(snapshots),
        "candidate_count": len(candidates),
        "signal_counts": dict(sorted(Counter(signal for row in snapshots for signal in row["signals"]).items())),
        "candidates": sorted(candidates, key=lambda row: (-row["candidate_score"], row["timestamp_ms"])),
        "controls": [row for row in snapshots if not row["signals"]],
    }


def _summarize_snapshot(
    archive: tarfile.TarFile,
    members: dict[str, tarfile.TarInfo],
    prefix: str,
    manifest: dict[str, Any],
    bundle_path: Path,
    incoming: Path,
    catalog_cache: dict[str, Any],
) -> dict[str, Any]:
    signals: set[str] = set()
    effects: set[str] = set()
    alert_count = vehicle_count = trip_count = skipped = 0
    failed = [row["name"] for row in manifest.get("feeds", []) if row.get("status") == "failed"]
    if failed:
        signals.add("missing_feed")
    for mode in ("bus", "rail"):
        catalog = None
        static_row = next(
            (row for row in manifest.get("feeds", []) if row.get("name") == f"{mode}_static_gtfs"),
            {},
        )
        static_member = members.get(str(static_row.get("path") or "")) or members.get(f"{prefix}/{mode}_gtfs.zip")
        if static_member:
            static_content = archive.extractfile(static_member).read()
            digest = hashlib.sha256(static_content).hexdigest()
            if digest not in catalog_cache:
                catalog_cache[digest] = load_gtfs_catalog(
                    static_content,
                    feed_label=f"LA Metro {mode}",
                    include_transfers=False,
                    include_shapes=False,
                )
            catalog = catalog_cache[digest]
        predicted_by_stop: dict[tuple[str, str], list[int]] = {}
        for payload_type, filename in (
            ("alerts", f"{mode}_alerts.pb"),
            ("vehicle_positions", f"{mode}_vehicle_positions.pb"),
            ("trip_updates", f"{mode}_trip_updates.pb"),
        ):
            member = members.get(f"{prefix}/{filename}")
            if not member:
                continue
            try:
                content = archive.extractfile(member).read()
                realtime = load_gtfs_realtime_resource(
                    content, feed_label="LA Metro", collection_source=f"archive:{mode}", payload_type=payload_type
                )
            except Exception:
                signals.add("malformed_feed")
                continue
            alert_count += len(realtime.alerts)
            vehicle_count += len(realtime.vehicles)
            trip_count += len(realtime.trip_updates)
            if (
                realtime.latest_timestamp_ms()
                and timestamp_ms_from_manifest(manifest) - realtime.latest_timestamp_ms() > 90_000
            ):
                signals.add("stale_feed")
            for alert in realtime.alerts:
                effect = str(alert.effect or "").upper()
                effects.add(effect)
                text = f"{alert.header_text or ''} {alert.description_text or ''}".lower()
                if effect in IMPACT_EFFECTS or any(
                    word in text for word in ("delay", "detour", "no service", "suspend")
                ):
                    signals.add("service_impact_alert")
            for update in realtime.trip_updates:
                delays = [
                    value
                    for stop in update.stop_time_updates
                    for value in (stop.arrival_delay_seconds, stop.departure_delay_seconds)
                    if value is not None
                ]
                if delays and max(delays) >= 300:
                    signals.add("predicted_delay_divergence")
                for stop in update.stop_time_updates:
                    predicted = stop.arrival_time_unix or stop.departure_time_unix
                    if predicted and stop.stop_id and update.route_id:
                        predicted_by_stop.setdefault((update.route_id, stop.stop_id), []).append(predicted)
                    if catalog and predicted:
                        scheduled = catalog.scheduled_epoch_seconds(
                            update.trip_id,
                            service_date=update.service_date,
                            timezone_name="America/Los_Angeles",
                            stop_sequence=stop.stop_sequence,
                            stop_id=stop.stop_id,
                            event="arrival" if stop.arrival_time_unix else "departure",
                        )
                        if scheduled is not None and predicted - scheduled >= 300:
                            signals.add("predicted_delay_divergence")
                skipped += sum(
                    1 for stop in update.stop_time_updates if str(stop.schedule_relationship or "").upper() == "SKIPPED"
                )
        if any(
            later - earlier <= 120
            for values in predicted_by_stop.values()
            for earlier, later in zip(sorted(set(values)), sorted(set(values))[1:])
        ):
            signals.add("headway_compression")
    if skipped:
        signals.add("skipped_stops")
    timestamp_ms = int(manifest.get("timestamp_ms") or 0)
    return {
        "timestamp_ms": timestamp_ms,
        "captured_at": manifest.get("captured_at"),
        "bundle": str(bundle_path.relative_to(incoming)),
        "snapshot_path": manifest.get("snapshot_path"),
        "mode_status": manifest.get("mode_status", {}),
        "failed_feeds": failed,
        "alert_count": alert_count,
        "alert_effects": sorted(effect for effect in effects if effect),
        "vehicle_count": vehicle_count,
        "trip_update_count": trip_count,
        "skipped_stop_count": skipped,
        "signals": sorted(signals),
        "candidate_score": len(signals) + (2 if "service_impact_alert" in signals else 0),
        "control_stratum": _control_stratum(timestamp_ms),
    }


def timestamp_ms_from_manifest(manifest: dict[str, Any]) -> int:
    return int(manifest.get("timestamp_ms") or 0)


def _window(timestamp_ms: int) -> dict[str, str]:
    center = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    from datetime import timedelta

    return {
        "start": (center - timedelta(minutes=15)).isoformat(),
        "center": center.isoformat(),
        "end": (center + timedelta(minutes=15)).isoformat(),
    }


def _control_stratum(timestamp_ms: int) -> str:
    local = datetime.fromtimestamp(timestamp_ms / 1000, tz=ZoneInfo("America/Los_Angeles"))
    if local.weekday() >= 5:
        return "weekend"
    if local.hour < 5 or local.hour >= 22:
        return "late_night"
    if 6 <= local.hour < 10 or 15 <= local.hour < 19:
        return "weekday_peak"
    return "weekday_off_peak"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--incoming", default="data/case-pack-sources/lametro/incoming")
    parser.add_argument("--output", default="data/case-pack-sources/lametro/catalog/candidates.json")
    args = parser.parse_args()
    payload = build_index(Path(args.incoming).resolve())
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
