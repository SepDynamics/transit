import json

import pytest


manifold_engine = pytest.importorskip("manifold_engine")


def test_manifold_engine_zero_max_windows_uses_default_cap() -> None:
    payload = json.loads(
        manifold_engine.analyze_bytes(
            b"\x01" * 5000,
            window_bytes=1,
            step_bytes=1,
            max_windows=0,
        )
    )

    assert payload["config"]["max_windows"] == 4096
    assert payload["summary"]["total_windows"] == 4096


def test_manifold_engine_clamps_excessive_max_windows() -> None:
    payload = json.loads(
        manifold_engine.analyze_bytes(
            b"\x01" * 20000,
            window_bytes=1,
            step_bytes=1,
            max_windows=999999,
        )
    )

    assert payload["config"]["max_windows"] == 16384
    assert payload["summary"]["total_windows"] == 16384
