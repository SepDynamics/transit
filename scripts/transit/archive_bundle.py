#!/usr/bin/env python3
"""Package completed hourly transit snapshots and remove verified sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


def completed_hours(root: Path, now: datetime, grace_minutes: int) -> list[datetime]:
    cutoff = now.astimezone(timezone.utc) - timedelta(minutes=grace_minutes)
    hours = set()
    for manifest in root.glob("archive/*/*/*/*/manifest.json"):
        try:
            value = datetime.strptime(
                "/".join(manifest.parent.relative_to(root / "archive").parts), "%Y/%m/%d/%H%M%SZ"
            ).replace(tzinfo=timezone.utc)
        except (ValueError, OSError):
            continue
        hour = value.replace(minute=0, second=0, microsecond=0)
        if hour + timedelta(hours=1) <= cutoff:
            hours.add(hour)
    return sorted(hours)


def bundle_hour(root: Path, hour: datetime, min_free_gb: float = 3) -> Path | None:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < int(min_free_gb * 1024**3):
        raise RuntimeError(f"bundle disk guard: less than {min_free_gb:.2f} GiB free")
    archive_day = root / "archive" / hour.strftime("%Y/%m/%d")
    snapshots = sorted(path for path in archive_day.glob(f"{hour:%H}[0-5][0-9][0-5][0-9]Z") if path.is_dir())
    if not snapshots:
        return None
    destination = root / "transfer" / hour.strftime("%Y/%m/%d") / f"{hour:%H}.tar.gz"
    checksum_path = destination.with_suffix(destination.suffix + ".sha256")
    if destination.exists() and checksum_path.exists():
        _validate(destination, checksum_path)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{hour:%H}.", suffix=".tar.gz", dir=destination.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        with tarfile.open(temporary, "w:gz", compresslevel=6) as bundle:
            included = set()
            for snapshot in snapshots:
                bundle.add(snapshot, arcname=str(snapshot.relative_to(root)), recursive=True)
                included.add(snapshot.resolve())
                manifest_path = snapshot / "manifest.json"
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    manifest = {}
                for row in manifest.get("feeds", []):
                    referenced = root / str(row.get("path") or "")
                    if (
                        not referenced.is_file()
                        or referenced.resolve() in included
                        or any(referenced.resolve().is_relative_to(item.resolve()) for item in snapshots)
                    ):
                        continue
                    bundle.add(referenced, arcname=str(referenced.relative_to(root)), recursive=False)
                    included.add(referenced.resolve())
        with tarfile.open(temporary, "r:gz") as bundle:
            names = bundle.getnames()
            if not names or not any(name.endswith("/manifest.json") for name in names):
                raise RuntimeError("bundle validation failed: no snapshot manifests")
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        temporary.replace(destination)
        checksum_path.write_text(f"{digest}  {destination.name}\n", encoding="utf-8")
        _validate(destination, checksum_path)
        for snapshot in snapshots:
            shutil.rmtree(snapshot)
        _remove_empty_parents(archive_day, root / "archive")
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def _validate(bundle: Path, checksum_path: Path) -> None:
    expected = checksum_path.read_text(encoding="utf-8").split()[0]
    actual = hashlib.sha256(bundle.read_bytes()).hexdigest()
    if actual != expected:
        raise RuntimeError(f"checksum mismatch for {bundle}")
    with tarfile.open(bundle, "r:gz") as archive:
        archive.getmembers()


def _remove_empty_parents(path: Path, stop: Path) -> None:
    current = path
    while current != stop and current.is_dir() and not any(current.iterdir()):
        current.rmdir()
        current = current.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-dir", default="data/feeds/lametro")
    parser.add_argument("--hour", help="UTC hour as YYYY-MM-DDTHH")
    parser.add_argument("--grace-minutes", type=int, default=2)
    parser.add_argument("--min-free-gb", type=float, default=3)
    args = parser.parse_args()
    root = Path(args.root_dir)
    hours = (
        [datetime.strptime(args.hour, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)]
        if args.hour
        else completed_hours(root, datetime.now(timezone.utc), max(0, args.grace_minutes))
    )
    for hour in hours:
        result = bundle_hour(root, hour, max(0, args.min_free_gb))
        if result:
            print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
