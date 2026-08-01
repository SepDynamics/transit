#!/usr/bin/env python3
"""Promote selected, verified LA Metro snapshots into a self-contained case pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.transit.pull_lametro import verify_bundle


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_selection(value: str) -> tuple[Path, str]:
    try:
        bundle, snapshot = value.split("#", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("selection must be BUNDLE#SNAPSHOT_PATH") from exc
    snapshot_path = PurePosixPath(snapshot)
    if (
        snapshot_path.is_absolute()
        or ".." in snapshot_path.parts
        or len(snapshot_path.parts) != 5
        or snapshot_path.parts[0] != "archive"
    ):
        raise argparse.ArgumentTypeError(f"unsafe snapshot path: {snapshot}")
    return Path(bundle), snapshot_path.as_posix()


def _safe_member_name(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe archive member: {name}")
    return path.as_posix().rstrip("/")


def _write_member(archive: tarfile.TarFile, member: tarfile.TarInfo, destination: Path) -> None:
    name = _safe_member_name(member.name)
    if not name or member.isdir():
        return
    if not member.isfile():
        raise ValueError(f"unsupported archive member type: {member.name}")
    source = archive.extractfile(member)
    if source is None:
        raise ValueError(f"could not read archive member: {member.name}")
    target = destination / name
    target.parent.mkdir(parents=True, exist_ok=True)
    with source, target.open("wb") as output:
        shutil.copyfileobj(source, output)


def write_checksums(destination: Path) -> None:
    files = sorted(
        path
        for path in destination.rglob("*")
        if path.is_file() and path.name != "checksums.sha256"
    )
    checksum_lines = [
        f"{sha256_file(path)}  {path.relative_to(destination).as_posix()}"
        for path in files
    ]
    (destination / "checksums.sha256").write_text(
        "\n".join(checksum_lines) + "\n", encoding="utf-8"
    )


def promote(selections: list[tuple[Path, str]], destination: Path) -> dict[str, Any]:
    destination = destination.resolve()
    allowed_root = (Path.cwd() / "data/case-packs/lametro").resolve()
    if destination == allowed_root or allowed_root not in destination.parents:
        raise ValueError(f"destination must be a child of {allowed_root}")
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"destination is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    source_bundles: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    for bundle, snapshot_path in selections:
        bundle = bundle.resolve()
        if not verify_bundle(bundle):
            raise ValueError(f"bundle checksum verification failed: {bundle}")
        manifest_name = f"{snapshot_path}/manifest.json"
        with tarfile.open(bundle, "r:gz") as archive:
            members = {_safe_member_name(member.name): member for member in archive.getmembers()}
            manifest_member = members.get(manifest_name)
            if manifest_member is None:
                raise FileNotFoundError(f"{manifest_name} not found in {bundle}")
            manifest_file = archive.extractfile(manifest_member)
            if manifest_file is None:
                raise ValueError(f"could not read {manifest_name}")
            manifest = json.load(manifest_file)
            selected_names = {
                name for name in members if name == snapshot_path or name.startswith(f"{snapshot_path}/")
            }
            for feed in manifest.get("feeds") or []:
                feed_path = feed.get("path")
                if feed_path:
                    selected_names.add(_safe_member_name(str(feed_path)))
            for name in sorted(selected_names):
                member = members.get(name)
                if member is None:
                    raise FileNotFoundError(f"manifest referenced missing member {name} in {bundle}")
                _write_member(archive, member, destination)

        source_bundles.append(
            {
                "path": str(bundle),
                "sha256": sha256_file(bundle),
                "checksum_path": str(bundle.with_suffix(bundle.suffix + ".sha256")),
            }
        )
        snapshots.append(
            {
                "snapshot_path": snapshot_path,
                "captured_at": manifest.get("captured_at"),
                "timestamp_ms": manifest.get("timestamp_ms"),
                "mode_status": manifest.get("mode_status"),
                "feeds": [
                    {
                        key: feed.get(key)
                        for key in ("name", "mode", "status", "path", "sha256", "url", "error")
                        if feed.get(key) is not None
                    }
                    for feed in manifest.get("feeds") or []
                ],
            }
        )

    source_manifest = {
        "schema_version": 1,
        "agency_key": "lametro",
        "agency_name": "LA Metro",
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "integrity": "Every source bundle was verified against its adjacent SHA-256 file before extraction.",
        "source_bundles": source_bundles,
        "snapshots": snapshots,
    }
    manifest_path = destination / "source_manifest.json"
    manifest_path.write_text(json.dumps(source_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_checksums(destination)
    return source_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--selection", action="append", type=parse_selection)
    parser.add_argument(
        "--refresh-checksums",
        action="store_true",
        help="refresh checksums for an existing promoted pack without extracting",
    )
    args = parser.parse_args()
    if args.refresh_checksums:
        write_checksums(args.destination.resolve())
        print(f"refreshed_checksums destination={args.destination}")
        return 0
    if not args.selection:
        parser.error("at least one --selection is required unless --refresh-checksums is used")
    result = promote(args.selection, args.destination)
    print(f"promoted_snapshots={len(result['snapshots'])} destination={args.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
