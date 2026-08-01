#!/usr/bin/env python3
"""Rotate, pull, verify, and prune durable evidence from the LA droplet."""

from __future__ import annotations

import argparse
import hashlib
import shlex
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


EVIDENCE_FILENAME = "operational_snapshots.jsonl"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evidence_relative_path(remote_path: str, remote_root: str) -> Path:
    relative = Path(remote_path).relative_to(Path(remote_root))
    if len(relative.parts) != 3:
        raise ValueError(f"unexpected evidence path: {remote_path}")
    agency, partition, filename = relative.parts
    if not agency.startswith("agency=") or not partition.startswith("date="):
        raise ValueError(f"unexpected evidence partition: {remote_path}")
    datetime.strptime(partition.removeprefix("date="), "%Y-%m-%d")
    if not (
        filename == EVIDENCE_FILENAME
        or filename.startswith("operational_snapshots.transfer-")
        and filename.endswith(".jsonl")
    ):
        raise ValueError(f"unexpected evidence filename: {remote_path}")
    return relative


def run_ssh(remote: str, command: str, *, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ssh", remote, command],
        check=True,
        capture_output=capture_output,
        text=True,
    )


def rotate_remote(remote: str, remote_root: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    command = (
        f"find {shlex.quote(remote_root)} -mindepth 3 -maxdepth 3 -type f "
        f"-name {shlex.quote(EVIDENCE_FILENAME)} -print0 | "
        "while IFS= read -r -d '' path; do "
        f"mv -- \"$path\" \"${{path%.jsonl}}.transfer-{stamp}.jsonl\"; "
        "done"
    )
    run_ssh(remote, f"bash -c {shlex.quote(command)}")


def list_remote_segments(remote: str, remote_root: str) -> list[str]:
    command = (
        f"find {shlex.quote(remote_root)} -mindepth 3 -maxdepth 3 -type f "
        "-name 'operational_snapshots.transfer-*.jsonl' -print"
    )
    result = run_ssh(remote, command, capture_output=True)
    paths = sorted(line for line in result.stdout.splitlines() if line)
    for path in paths:
        evidence_relative_path(path, remote_root)
    return paths


def remote_fingerprint(remote: str, remote_path: str) -> tuple[int, str]:
    quoted = shlex.quote(remote_path)
    result = run_ssh(
        remote,
        f"stat -c '%s' -- {quoted} && sha256sum -- {quoted}",
        capture_output=True,
    )
    lines = result.stdout.splitlines()
    if len(lines) != 2:
        raise RuntimeError(f"could not fingerprint remote evidence: {remote_path}")
    return int(lines[0]), lines[1].split()[0]


def transfer_verified_segments(
    remote: str,
    remote_root: str,
    local_root: Path,
    *,
    min_free_gb: float,
) -> int:
    local_root.mkdir(parents=True, exist_ok=True)
    minimum_free = int(min_free_gb * 1024**3)
    transferred = 0
    for remote_path in list_remote_segments(remote, remote_root):
        relative = evidence_relative_path(remote_path, remote_root)
        local_path = local_root / relative
        local_path.parent.mkdir(parents=True, exist_ok=True)
        remote_size, remote_digest = remote_fingerprint(remote, remote_path)
        if shutil.disk_usage(local_root).free < remote_size + minimum_free:
            raise RuntimeError(
                f"local disk guard: evidence requires {remote_size} bytes plus "
                f"{min_free_gb:.2f} GiB reserve"
            )
        subprocess.run(
            [
                "rsync",
                "-a",
                "--partial",
                "--checksum",
                f"{remote}:{remote_path}",
                str(local_path),
            ],
            check=True,
        )
        final_size, final_digest = remote_fingerprint(remote, remote_path)
        if (final_size, final_digest) != (remote_size, remote_digest):
            raise RuntimeError(f"remote evidence changed during transfer: {remote_path}")
        if local_path.stat().st_size != remote_size or sha256_file(local_path) != remote_digest:
            raise RuntimeError(f"local evidence verification failed: {local_path}")
        run_ssh(remote, f"rm -- {shlex.quote(remote_path)}")
        transferred += 1
    return transferred


def prune_local(local_root: Path, retention_days: int, *, today: date | None = None) -> int:
    cutoff = (today or datetime.now(timezone.utc).date()) - timedelta(days=retention_days)
    pruned = 0
    for partition in local_root.glob("agency=*/date=*"):
        try:
            partition_date = datetime.strptime(
                partition.name.removeprefix("date="), "%Y-%m-%d"
            ).date()
        except ValueError:
            continue
        if partition_date >= cutoff or partition.is_symlink() or not partition.is_dir():
            continue
        shutil.rmtree(partition)
        pruned += 1
    return pruned


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote", default="root@161.35.226.210")
    parser.add_argument("--remote-root", default="/root/transit/data/evidence")
    parser.add_argument("--local-root", default="data/evidence")
    parser.add_argument("--min-free-gb", type=float, default=8)
    parser.add_argument("--local-retention-days", type=int, default=8)
    parser.add_argument("--settle-seconds", type=float, default=2)
    args = parser.parse_args()
    rotate_remote(args.remote, args.remote_root)
    time.sleep(max(0, args.settle_seconds))
    transferred = transfer_verified_segments(
        args.remote,
        args.remote_root,
        Path(args.local_root).resolve(),
        min_free_gb=max(0, args.min_free_gb),
    )
    local_pruned = prune_local(
        Path(args.local_root).resolve(), max(1, args.local_retention_days)
    )
    print(f"verified_and_remote_pruned={transferred} local_pruned={local_pruned}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
