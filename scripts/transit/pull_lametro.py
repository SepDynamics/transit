#!/usr/bin/env python3
"""Resumably pull LA bundles, verify them, and apply guarded retention."""

from __future__ import annotations

import argparse
import hashlib
import shlex
import shutil
import subprocess
import time
from pathlib import Path


def verify_bundle(path: Path) -> bool:
    checksum = path.with_suffix(path.suffix + ".sha256")
    if not checksum.is_file():
        return False
    expected = checksum.read_text(encoding="utf-8").split()[0]
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected


def pull(remote: str, remote_root: str, local_root: Path, min_free_gb: float) -> list[Path]:
    local_root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(local_root).free
    if free < int(min_free_gb * 1024**3):
        raise RuntimeError(f"local disk guard: {free / 1024**3:.2f} GiB free; requires {min_free_gb:.2f} GiB")
    subprocess.run(
        [
            "rsync",
            "-av",
            "--partial",
            "--checksum",
            f"{remote}:{remote_root.rstrip('/')}/",
            f"{local_root}/",
        ],
        check=True,
    )
    bundles = sorted(local_root.glob("*/*/*/*.tar.gz"))
    invalid = [path for path in bundles if not verify_bundle(path)]
    if invalid:
        raise RuntimeError("unverified bundles: " + ", ".join(str(path) for path in invalid))
    return bundles


def prune_remote_verified(remote: str, remote_root: str, local_root: Path, retention_days: int) -> int:
    command = f"find {shlex.quote(remote_root)} -type f -name '*.tar.gz' -mtime +{int(retention_days)} -print"
    result = subprocess.run(["ssh", remote, command], check=True, capture_output=True, text=True)
    removable: list[str] = []
    for remote_path in result.stdout.splitlines():
        relative = Path(remote_path).relative_to(Path(remote_root))
        local_path = local_root / relative
        if local_path.is_file() and verify_bundle(local_path):
            removable.extend((remote_path, remote_path + ".sha256"))
    if removable:
        quoted = " ".join(shlex.quote(path) for path in removable)
        subprocess.run(["ssh", remote, f"rm -- {quoted}"], check=True)
    return len(removable) // 2


def prune_local(local_root: Path, retention_days: int) -> int:
    cutoff = time.time() - retention_days * 86400
    count = 0
    for bundle in local_root.glob("*/*/*/*.tar.gz"):
        if bundle.stat().st_mtime >= cutoff or not verify_bundle(bundle):
            continue
        bundle.with_suffix(bundle.suffix + ".sha256").unlink()
        bundle.unlink()
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote", default="root@161.35.226.210")
    parser.add_argument("--remote-root", default="/root/transit/data/feeds/lametro/transfer")
    parser.add_argument("--local-root", default="data/case-pack-sources/lametro/incoming")
    parser.add_argument("--min-free-gb", type=float, default=8)
    parser.add_argument("--remote-retention-days", type=int, default=3)
    parser.add_argument("--local-retention-days", type=int, default=8)
    args = parser.parse_args()
    local_root = Path(args.local_root).resolve()
    bundles = pull(args.remote, args.remote_root, local_root, max(0, args.min_free_gb))
    remote_pruned = prune_remote_verified(args.remote, args.remote_root, local_root, max(1, args.remote_retention_days))
    local_pruned = prune_local(local_root, max(1, args.local_retention_days))
    print(f"verified={len(bundles)} remote_pruned={remote_pruned} local_pruned={local_pruned}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
