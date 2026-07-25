#!/usr/bin/env python3
"""Small deployment smoke check for public Sentinel endpoints."""

from __future__ import annotations

import argparse
import json
import sys
from urllib.request import urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Transit Sentinel HTTP contracts")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    endpoints = ("/health", "/api/status/network", "/api/status/feed-quality")
    for endpoint in endpoints:
        with urlopen(f"{base}{endpoint}", timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"{endpoint} did not return a JSON object")
        print(f"ok {endpoint}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"smoke check failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
