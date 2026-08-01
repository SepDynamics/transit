from datetime import date

import pytest

from scripts.transit.pull_lametro_evidence import (
    evidence_relative_path,
    prune_local,
    sha256_file,
)


def test_evidence_relative_path_accepts_live_and_rotated_names():
    root = "/root/transit/data/evidence"
    assert evidence_relative_path(
        f"{root}/agency=lametro/date=2026-07-30/operational_snapshots.jsonl", root
    ).parts == (
        "agency=lametro",
        "date=2026-07-30",
        "operational_snapshots.jsonl",
    )
    assert evidence_relative_path(
        f"{root}/agency=lametro/date=2026-07-30/"
        "operational_snapshots.transfer-20260730T220000Z.jsonl",
        root,
    ).name.endswith(".jsonl")


def test_evidence_relative_path_rejects_unexpected_targets():
    root = "/root/transit/data/evidence"
    with pytest.raises(ValueError):
        evidence_relative_path(f"{root}/agency=lametro/secrets.env", root)


def test_sha256_and_local_retention(tmp_path):
    old = tmp_path / "agency=lametro" / "date=2026-07-20"
    recent = tmp_path / "agency=lametro" / "date=2026-07-29"
    old.mkdir(parents=True)
    recent.mkdir(parents=True)
    payload = recent / "operational_snapshots.transfer-test.jsonl"
    payload.write_bytes(b"evidence\n")
    (old / "operational_snapshots.transfer-test.jsonl").write_bytes(b"old\n")

    assert sha256_file(payload) == "bdcf4c994585af6dd6cb1cfbff78bcc73ab27dc30a299db5bb83766ca05b5de4"
    assert prune_local(tmp_path, 8, today=date(2026, 7, 30)) == 1
    assert not old.exists()
    assert recent.exists()
