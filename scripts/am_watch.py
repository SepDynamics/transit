#!/usr/bin/env python3
"""Background watcher: automatically ingest Codex sessions.

Watches ~/.codex/sessions/ for new or modified session files and pipes them
through agent-manifold's Codex normalizer.  On each change it ingests the
session, writes a canonical trace to traces/, and prints a one-line status.

Kilo does not currently emit Codex-style session JSONL here.  Kilo integration
is handled through project instructions plus the ``TracedAgent`` and
``agent-manifold collect kilo`` paths.

Usage
-----
Start in the background (from the project root):

    setsid -f python scripts/am_watch.py --historical-days 30 --poll > /tmp/am-watch.log 2>&1
    python scripts/am_watch.py --no-historical    # skip existing sessions

Stop with Ctrl-C or kill.

After collecting sessions, analyse them:

    agent-manifold timeline   traces/*.jsonl
    agent-manifold summary    traces/*.jsonl
    agent-manifold coverage   traces/*.jsonl
    agent-manifold validate   traces/*.jsonl --early-detection
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Locate project root regardless of where the script is invoked from.
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SCRIPT_DIR.parent

# Add project root to path so we can import agent_manifold even if it is not
# installed (e.g. dev checkout without pip install -e .).
sys.path.insert(0, str(PROJECT_ROOT))

from agent_manifold.collectors import ingest_codex_session  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
TRACES_DIR = PROJECT_ROOT / "traces"
CWD_FILTER = str(PROJECT_ROOT)  # Only ingest sessions from this project


def ingest_one(session_path: Path, *, verbose: bool = True) -> bool:
    """Ingest a single Codex session file.  Returns True if events were written."""
    try:
        sid, count = ingest_codex_session(
            session_path,
            output_path=TRACES_DIR / f"codex-{session_path.stem}.jsonl",
            cwd_filter=CWD_FILTER,
        )
    except Exception as exc:
        if verbose:
            print(f"[am-watch] error ingesting {session_path.name}: {exc}", flush=True)
        return False

    if sid is None:
        return False  # filtered out (different project)

    if count == 0:
        return False  # empty session

    if verbose:
        print(
            json.dumps(
                {
                    "session_id": sid,
                    "events": count,
                    "source": session_path.name,
                    "trace": f"traces/codex-{session_path.stem}.jsonl",
                }
            ),
            flush=True,
        )
    return True


def scan_historical(since_days: int = 7) -> int:
    """Ingest all recent Codex sessions on startup.  Returns session count."""
    if not CODEX_SESSIONS_DIR.exists():
        return 0

    cutoff = time.time() - since_days * 86400
    ingested = 0
    for p in sorted(CODEX_SESSIONS_DIR.rglob("*.jsonl")):
        if p.stat().st_mtime < cutoff:
            continue
        if ingest_one(p):
            ingested += 1
    return ingested


def watch(poll_interval: float = 2.0) -> None:
    """Poll-based watcher (no external dependencies beyond stdlib)."""
    print(f"[am-watch] watching {CODEX_SESSIONS_DIR}", flush=True)
    print(f"[am-watch] project filter: {CWD_FILTER}", flush=True)
    print(f"[am-watch] traces: {TRACES_DIR}", flush=True)

    seen: dict[Path, float] = {}

    # Seed the seen dict with existing files so we only react to new changes.
    for p in CODEX_SESSIONS_DIR.rglob("*.jsonl"):
        seen[p] = p.stat().st_mtime

    while True:
        time.sleep(poll_interval)
        try:
            for p in CODEX_SESSIONS_DIR.rglob("*.jsonl"):
                mtime = p.stat().st_mtime
                if seen.get(p) != mtime:
                    seen[p] = mtime
                    ingest_one(p)
        except Exception as exc:
            print(f"[am-watch] scan error: {exc}", flush=True)


def watch_with_watchdog(poll_interval: float = 1.0) -> None:
    """watchdog-based watcher (faster, event-driven)."""
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    class _Handler(FileSystemEventHandler):
        def on_created(self, event: FileSystemEvent) -> None:
            if not event.is_directory and str(event.src_path).endswith(".jsonl"):
                # Brief delay so the file has initial content before we read.
                time.sleep(0.5)
                ingest_one(Path(str(event.src_path)))

        def on_modified(self, event: FileSystemEvent) -> None:
            if not event.is_directory and str(event.src_path).endswith(".jsonl"):
                ingest_one(Path(str(event.src_path)))

    observer = Observer()
    observer.schedule(_Handler(), str(CODEX_SESSIONS_DIR), recursive=True)
    observer.start()

    print(f"[am-watch] watchdog watching {CODEX_SESSIONS_DIR}", flush=True)
    print(f"[am-watch] project filter: {CWD_FILTER}", flush=True)
    print(f"[am-watch] traces: {TRACES_DIR}", flush=True)

    try:
        while observer.is_alive():
            observer.join(timeout=1.0)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Watch Codex sessions and auto-ingest into agent-manifold traces."
    )
    parser.add_argument(
        "--no-historical",
        action="store_true",
        help="Skip ingesting existing sessions on startup.",
    )
    parser.add_argument(
        "--historical-days",
        type=int,
        default=7,
        help="How many days back to scan for historical sessions (default: 7).",
    )
    parser.add_argument(
        "--all-projects",
        action="store_true",
        help="Ingest sessions from all projects, not just this one.",
    )
    parser.add_argument(
        "--poll",
        action="store_true",
        help="Use polling instead of watchdog (slower but no extra deps).",
    )
    args = parser.parse_args()

    if args.all_projects:
        global CWD_FILTER
        CWD_FILTER = ""  # type: ignore[assignment]

    TRACES_DIR.mkdir(parents=True, exist_ok=True)

    if not args.no_historical:
        n = scan_historical(since_days=args.historical_days)
        print(f"[am-watch] ingested {n} historical session(s)", flush=True)

    if not CODEX_SESSIONS_DIR.exists():
        print(
            f"[am-watch] {CODEX_SESSIONS_DIR} does not exist — "
            "start Codex to create sessions",
            flush=True,
        )
        return 1

    try:
        if args.poll:
            raise ImportError  # force polling path
        watch_with_watchdog()
    except ImportError:
        watch()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
