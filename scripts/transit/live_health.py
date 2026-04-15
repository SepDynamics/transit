#!/usr/bin/env python3
"""One-command live host health report for Transit Sentinel."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_CONTAINERS = (
    "transit-sentinel-valkey",
    "transit-sentinel-archive",
    "transit-sentinel-ingest",
    "transit-sentinel-api",
    "transit-sentinel-frontend",
)
DEFAULT_API_HEALTH_URL = "http://127.0.0.1:8000/health"
DEFAULT_PUBLIC_STATUS_URL = "https://sepdynamics.co/api/status/network"
DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"
READ_MODEL_KEYS = (
    "transit:scorecard:live:last",
    "transit:trends:live:last",
    "transit:dashboard:live:last",
    "transit:status:network:last",
)


@dataclass
class Check:
    name: str
    status: str
    detail: str


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def main() -> int:
    args = build_parser().parse_args()
    report = build_report(args)
    if args.json:
        print(json.dumps(json_report(report), indent=2, sort_keys=True))
    else:
        print_report(report)
    return 1 if any(check.status == "fail" for check in report["checks"]) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report live Transit Sentinel host, container, Valkey, and API health."
    )
    parser.add_argument(
        "--api-health-url",
        default=os.getenv("TRANSIT_LIVE_HEALTH_API_URL", DEFAULT_API_HEALTH_URL),
        help=f"local API health URL (default: {DEFAULT_API_HEALTH_URL})",
    )
    parser.add_argument(
        "--public-status-url",
        default=os.getenv(
            "TRANSIT_LIVE_HEALTH_PUBLIC_URL", DEFAULT_PUBLIC_STATUS_URL
        ),
        help=f"public status endpoint URL (default: {DEFAULT_PUBLIC_STATUS_URL})",
    )
    parser.add_argument(
        "--redis-url",
        default=os.getenv("VALKEY_URL", os.getenv("REDIS_URL", DEFAULT_REDIS_URL)),
        help=f"host redis URL for non-Docker fallback (default: {DEFAULT_REDIS_URL})",
    )
    parser.add_argument(
        "--valkey-container",
        default=os.getenv("TRANSIT_VALKEY_CONTAINER", "transit-sentinel-valkey"),
        help="Valkey container name used for redis-cli checks.",
    )
    parser.add_argument(
        "--containers",
        default=",".join(DEFAULT_CONTAINERS),
        help="comma-separated container names to inspect.",
    )
    parser.add_argument(
        "--since",
        default=os.getenv("TRANSIT_LIVE_HEALTH_SINCE", "6 hours ago"),
        help='lookback for kernel OOM evidence (default: "6 hours ago").',
    )
    parser.add_argument(
        "--log-since",
        default=os.getenv("TRANSIT_LIVE_HEALTH_LOG_SINCE", "2h"),
        help='lookback for Docker log evidence (default: "2h").',
    )
    parser.add_argument(
        "--bigkey-limit",
        type=int,
        default=int(os.getenv("TRANSIT_LIVE_HEALTH_BIGKEY_LIMIT", "10")),
        help="number of largest Valkey keys to print.",
    )
    parser.add_argument(
        "--key-scan-limit",
        type=int,
        default=int(os.getenv("TRANSIT_LIVE_HEALTH_KEY_SCAN_LIMIT", "3000")),
        help="maximum Valkey keys to scan while estimating largest keys.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of the text report.",
    )
    return parser


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    containers = [item.strip() for item in str(args.containers).split(",") if item.strip()]
    report: dict[str, Any] = {
        "generated_at": _now_iso(),
        "hostname": socket.gethostname(),
        "checks": [],
        "containers": [],
        "largest_valkey_keys": [],
        "read_models": [],
    }

    docker_ok = shutil.which("docker") is not None
    report["checks"].append(
        Check("docker", "ok" if docker_ok else "warn", "docker CLI available" if docker_ok else "docker CLI not found")
    )

    container_rows = inspect_containers(containers) if docker_ok else []
    report["containers"] = container_rows
    report["checks"].append(container_check(container_rows, containers, docker_ok))

    host_memory = read_host_memory()
    report["host_memory"] = host_memory
    report["checks"].append(host_memory_check(host_memory))

    valkey_info = read_valkey_info(args, docker_ok=docker_ok)
    report["valkey_memory"] = valkey_info
    report["checks"].append(valkey_memory_check(valkey_info))

    report["largest_valkey_keys"] = largest_valkey_keys(
        args,
        docker_ok=docker_ok,
        limit=max(1, int(args.bigkey_limit)),
        scan_limit=max(1, int(args.key_scan_limit)),
    )

    report["read_models"] = read_model_status(args, docker_ok=docker_ok)

    api_health = http_check(str(args.api_health_url), label="api_health")
    report["api_health"] = api_health
    report["checks"].append(http_result_check("api_health", api_health))

    public_status = http_check(str(args.public_status_url), label="public_status")
    report["public_status"] = public_status
    report["checks"].append(http_result_check("public_status", public_status))

    report["oom_evidence"] = recent_oom_evidence(str(args.since))
    report["checks"].append(oom_check(report["oom_evidence"]))

    report["server_busy"] = server_busy_evidence(container_rows, str(args.log_since))
    report["checks"].append(server_busy_check(report["server_busy"]))

    return report


def inspect_containers(container_names: Iterable[str]) -> list[dict[str, Any]]:
    names = list(container_names)
    if not names:
        return []
    rows = []
    for requested_name in names:
        result = run_command(["docker", "inspect", requested_name], timeout=5)
        if result.returncode != 0:
            rows.append(
                {
                    "name": requested_name,
                    "present": False,
                    "error": result.stderr.strip() or result.stdout.strip(),
                }
            )
            continue
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            rows.append(
                {
                    "name": requested_name,
                    "present": False,
                    "error": "docker inspect returned invalid JSON",
                }
            )
            continue
        row = payload[0] if isinstance(payload, list) and payload else {}
        if not isinstance(row, dict):
            rows.append({"name": requested_name, "present": False})
            continue
        name = str(row.get("Name") or requested_name).lstrip("/")
        state = row.get("State") if isinstance(row.get("State"), dict) else {}
        host_config = row.get("HostConfig") if isinstance(row.get("HostConfig"), dict) else {}
        rows.append(
            {
            "name": name,
            "present": True,
            "status": state.get("Status"),
            "running": bool(state.get("Running")),
            "oom_killed": bool(state.get("OOMKilled")),
            "restart_count": int(row.get("RestartCount") or 0),
            "exit_code": state.get("ExitCode"),
            "started_at": state.get("StartedAt"),
            "finished_at": state.get("FinishedAt"),
            "memory_limit_bytes": int(host_config.get("Memory") or 0),
            "error": state.get("Error") or "",
            }
        )
    return rows


def read_host_memory() -> dict[str, Any]:
    try:
        raw = open("/proc/meminfo", encoding="utf-8").read().splitlines()
    except OSError as exc:
        return {"error": str(exc)}
    values: dict[str, int] = {}
    for line in raw:
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(":"):
            key = parts[0].rstrip(":")
            try:
                values[key] = int(parts[1]) * 1024
            except ValueError:
                pass
    mem_total = values.get("MemTotal", 0)
    mem_available = values.get("MemAvailable", 0)
    swap_total = values.get("SwapTotal", 0)
    swap_free = values.get("SwapFree", 0)
    return {
        "mem_total_bytes": mem_total,
        "mem_available_bytes": mem_available,
        "mem_used_bytes": max(0, mem_total - mem_available),
        "mem_used_pct": _pct(mem_total - mem_available, mem_total),
        "swap_total_bytes": swap_total,
        "swap_used_bytes": max(0, swap_total - swap_free),
        "swap_used_pct": _pct(swap_total - swap_free, swap_total),
    }


def read_valkey_info(args: argparse.Namespace, *, docker_ok: bool) -> dict[str, Any]:
    result = redis_cli(args, ["INFO", "MEMORY"], docker_ok=docker_ok, timeout=8)
    if result.returncode != 0:
        return {"error": (result.stderr or result.stdout).strip()}
    info: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if ":" in line and not line.startswith("#"):
            key, value = line.split(":", 1)
            info[key.strip()] = value.strip()
    return {
        "used_memory_bytes": _int(info.get("used_memory")),
        "used_memory_human": info.get("used_memory_human"),
        "used_memory_rss_human": info.get("used_memory_rss_human"),
        "used_memory_peak_human": info.get("used_memory_peak_human"),
        "mem_fragmentation_ratio": _float(info.get("mem_fragmentation_ratio")),
        "maxmemory_bytes": _int(info.get("maxmemory")),
        "raw": info,
    }


def largest_valkey_keys(
    args: argparse.Namespace,
    *,
    docker_ok: bool,
    limit: int,
    scan_limit: int,
) -> list[dict[str, Any]]:
    if docker_ok and args.valkey_container:
        shell = (
            "redis-cli --raw --scan --pattern '*' "
            f"| head -n {int(scan_limit)} "
            "| while IFS= read -r key; do "
            "bytes=$(redis-cli MEMORY USAGE \"$key\" 2>/dev/null || true); "
            "case \"$bytes\" in ''|*[!0-9]*) bytes=0;; esac; "
            "printf '%s\\t%s\\n' \"$bytes\" \"$key\"; "
            "done "
            "| sort -nr "
            f"| head -n {int(limit)}"
        )
        result = run_command(
            ["docker", "exec", str(args.valkey_container), "sh", "-lc", shell],
            timeout=20,
        )
    else:
        result = run_command(
            ["redis-cli", "-u", str(args.redis_url), "--raw", "--scan", "--pattern", "*"],
            timeout=20,
        )
        if result.returncode == 0:
            keys = result.stdout.splitlines()[:scan_limit]
            rows = []
            for key in keys:
                usage = redis_cli(
                    args,
                    ["MEMORY", "USAGE", key],
                    docker_ok=False,
                    timeout=3,
                )
                rows.append(f"{_int(usage.stdout.strip())}\t{key}")
            rows.sort(key=lambda row: _int(row.split("\t", 1)[0]), reverse=True)
            result = CommandResult(0, "\n".join(rows[:limit]), "")
    if result.returncode != 0:
        return [{"error": (result.stderr or result.stdout).strip()}]
    rows = []
    for line in result.stdout.splitlines():
        if "\t" not in line:
            continue
        size_raw, key = line.split("\t", 1)
        rows.append({"key": key, "bytes": _int(size_raw), "human": human_bytes(_int(size_raw))})
    return rows


def read_model_status(args: argparse.Namespace, *, docker_ok: bool) -> list[dict[str, Any]]:
    rows = []
    for key in READ_MODEL_KEYS:
        result = redis_cli(args, ["GET", key], docker_ok=docker_ok, timeout=5)
        if result.returncode != 0:
            rows.append({"key": key, "present": False, "error": result.stderr.strip()})
            continue
        if not result.stdout.strip():
            rows.append({"key": key, "present": False})
            continue
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            rows.append({"key": key, "present": True, "error": "invalid JSON"})
            continue
        if not isinstance(payload, dict):
            rows.append({"key": key, "present": True, "error": "not an object"})
            continue
        generated_at = (
            (payload.get("read_model") or {}).get("generated_at")
            if isinstance(payload.get("read_model"), dict)
            else None
        ) or payload.get("generated_at")
        rows.append(
            {
                "key": key,
                "present": True,
                "generated_at": generated_at,
                "age_seconds": age_seconds(generated_at),
                "bytes": len(result.stdout.encode("utf-8")),
            }
        )
    return rows


def http_check(url: str, *, label: str) -> dict[str, Any]:
    started = time.monotonic()
    try:
        request = Request(url, headers={"User-Agent": "transit-live-health/1.0"})
        with urlopen(request, timeout=8) as response:
            body = response.read(512_000)
            elapsed_ms = round((time.monotonic() - started) * 1000, 1)
            payload: Any = None
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = None
            return {
                "label": label,
                "url": url,
                "ok": 200 <= int(response.status) < 400,
                "status_code": int(response.status),
                "latency_ms": elapsed_ms,
                "json": isinstance(payload, dict),
                "payload": payload if isinstance(payload, dict) else {},
            }
    except HTTPError as exc:
        return {
            "label": label,
            "url": url,
            "ok": False,
            "status_code": int(exc.code),
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "error": str(exc),
        }
    except (OSError, URLError) as exc:
        return {
            "label": label,
            "url": url,
            "ok": False,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "error": str(exc),
        }


def recent_oom_evidence(since: str) -> dict[str, Any]:
    if not shutil.which("journalctl"):
        return {"available": False, "error": "journalctl not found", "matches": []}
    result = run_command(["journalctl", "-k", "--since", since, "--no-pager"], timeout=8)
    if result.returncode != 0:
        return {
            "available": False,
            "error": (result.stderr or result.stdout).strip(),
            "matches": [],
        }
    matcher = re.compile(r"out of memory|oom-kill|killed process", re.IGNORECASE)
    matches = [line for line in result.stdout.splitlines() if matcher.search(line)]
    return {"available": True, "matches": matches[-12:], "count": len(matches)}


def server_busy_evidence(containers: list[dict[str, Any]], log_since: str) -> dict[str, Any]:
    names = [
        str(row.get("name"))
        for row in containers
        if row.get("present") and row.get("name") in {"transit-sentinel-api", "transit-sentinel-frontend"}
    ]
    if not shutil.which("docker") or not names:
        return {"available": False, "count": 0, "containers": {}}
    matcher = re.compile(r"server_busy|(?:\s|^)503(?:\s|$)")
    counts: dict[str, int] = {}
    samples: list[str] = []
    for name in names:
        result = run_command(["docker", "logs", "--since", log_since, name], timeout=8)
        if result.returncode != 0:
            counts[name] = -1
            continue
        text = "\n".join([result.stdout, result.stderr])
        lines = [line for line in text.splitlines() if matcher.search(line)]
        counts[name] = len(lines)
        samples.extend(f"{name}: {line}" for line in lines[-4:])
    return {
        "available": True,
        "count": sum(value for value in counts.values() if value > 0),
        "containers": counts,
        "samples": samples[-8:],
    }


def redis_cli(
    args: argparse.Namespace,
    command: list[str],
    *,
    docker_ok: bool,
    timeout: float,
) -> CommandResult:
    if docker_ok and args.valkey_container:
        return run_command(
            ["docker", "exec", str(args.valkey_container), "redis-cli", *command],
            timeout=timeout,
        )
    if not shutil.which("redis-cli"):
        return CommandResult(127, "", "redis-cli not found")
    return run_command(["redis-cli", "-u", str(args.redis_url), *command], timeout=timeout)


def run_command(command: list[str], *, timeout: float) -> CommandResult:
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(124, "", str(exc))
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def container_check(
    rows: list[dict[str, Any]],
    expected: list[str],
    docker_ok: bool,
) -> Check:
    if not docker_ok:
        return Check("containers", "warn", "skipped because docker is unavailable")
    missing = [name for name in expected if not any(row.get("name") == name and row.get("present") for row in rows)]
    stopped = [row["name"] for row in rows if row.get("present") and not row.get("running")]
    oom = [row["name"] for row in rows if row.get("oom_killed")]
    if missing or stopped or oom:
        detail = []
        if missing:
            detail.append(f"missing={','.join(missing)}")
        if stopped:
            detail.append(f"stopped={','.join(stopped)}")
        if oom:
            detail.append(f"oom_killed={','.join(oom)}")
        return Check("containers", "fail", " ".join(detail))
    restarted = [row for row in rows if int(row.get("restart_count") or 0) > 0]
    if restarted:
        detail = ", ".join(f"{row['name']}={row['restart_count']}" for row in restarted)
        return Check("containers", "warn", f"running with restart evidence: {detail}")
    return Check("containers", "ok", f"{len(rows)} expected containers running")


def host_memory_check(memory: dict[str, Any]) -> Check:
    if memory.get("error"):
        return Check("host_memory", "warn", str(memory["error"]))
    mem_pct = float(memory.get("mem_used_pct") or 0.0)
    swap_pct = float(memory.get("swap_used_pct") or 0.0)
    detail = (
        f"RAM {human_bytes(memory.get('mem_used_bytes'))}/"
        f"{human_bytes(memory.get('mem_total_bytes'))} used ({mem_pct:.1f}%), "
        f"swap {human_bytes(memory.get('swap_used_bytes'))}/"
        f"{human_bytes(memory.get('swap_total_bytes'))} used ({swap_pct:.1f}%)"
    )
    if mem_pct >= 92.0 or swap_pct >= 60.0:
        return Check("host_memory", "fail", detail)
    if mem_pct >= 85.0 or swap_pct >= 25.0:
        return Check("host_memory", "warn", detail)
    return Check("host_memory", "ok", detail)


def valkey_memory_check(info: dict[str, Any]) -> Check:
    if info.get("error"):
        return Check("valkey_memory", "fail", str(info["error"]))
    used = int(info.get("used_memory_bytes") or 0)
    maxmemory = int(info.get("maxmemory_bytes") or 0)
    frag = info.get("mem_fragmentation_ratio")
    detail = (
        f"used={info.get('used_memory_human') or human_bytes(used)} "
        f"rss={info.get('used_memory_rss_human') or 'unknown'} "
        f"peak={info.get('used_memory_peak_human') or 'unknown'} "
        f"fragmentation={frag if frag is not None else 'unknown'}"
    )
    if maxmemory > 0:
        pct = _pct(used, maxmemory)
        detail = f"{detail} maxmemory={human_bytes(maxmemory)} ({pct:.1f}%)"
        if pct >= 90.0:
            return Check("valkey_memory", "fail", detail)
        if pct >= 75.0:
            return Check("valkey_memory", "warn", detail)
    if frag is not None and frag >= 2.5:
        return Check("valkey_memory", "warn", detail)
    return Check("valkey_memory", "ok", detail)


def http_result_check(name: str, result: dict[str, Any]) -> Check:
    if not result.get("ok"):
        return Check(name, "fail", f"{result.get('url')} failed: {result.get('error') or result.get('status_code')}")
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    status = payload.get("status") or payload.get("severity") or "ok"
    latency = float(result.get("latency_ms") or 0.0)
    detail = f"{result.get('url')} {result.get('status_code')} {latency:.1f} ms status={status}"
    if latency >= 2500.0:
        return Check(name, "warn", detail)
    return Check(name, "ok", detail)


def oom_check(evidence: dict[str, Any]) -> Check:
    if not evidence.get("available"):
        return Check("recent_oom", "warn", str(evidence.get("error") or "unavailable"))
    count = int(evidence.get("count") or 0)
    if count:
        return Check("recent_oom", "fail", f"{count} kernel OOM lines found")
    return Check("recent_oom", "ok", "no recent kernel OOM evidence")


def server_busy_check(evidence: dict[str, Any]) -> Check:
    if not evidence.get("available"):
        return Check("server_busy_503", "warn", "Docker logs unavailable")
    count = int(evidence.get("count") or 0)
    if count >= 10:
        return Check("server_busy_503", "warn", f"{count} recent 503/server_busy log matches")
    return Check("server_busy_503", "ok", f"{count} recent 503/server_busy log matches")


def print_report(report: dict[str, Any]) -> None:
    checks: list[Check] = report["checks"]
    overall = "fail" if any(check.status == "fail" for check in checks) else "warn" if any(check.status == "warn" for check in checks) else "ok"
    print("Transit Sentinel live health")
    print(f"Generated: {report['generated_at']} on {report['hostname']}")
    print(f"Overall: {overall.upper()}")
    print()
    print("Checks")
    for check in checks:
        print(f"  {check.status.upper():4} {check.name}: {check.detail}")
    print()
    print("Containers")
    for row in report.get("containers") or []:
        if not row.get("present"):
            print(f"  MISSING {row.get('name')}")
            continue
        mem_limit = int(row.get("memory_limit_bytes") or 0)
        print(
            f"  {str(row.get('status') or 'unknown').upper():8} "
            f"{row.get('name')} restarts={row.get('restart_count')} "
            f"oom={row.get('oom_killed')} mem_limit={human_bytes(mem_limit) if mem_limit else 'unlimited'}"
        )
    print()
    print("Largest Valkey Keys")
    for row in report.get("largest_valkey_keys") or []:
        if row.get("error"):
            print(f"  unavailable: {row['error']}")
            continue
        print(f"  {row['human']:>9} {row['key']}")
    print()
    print("Read Models")
    for row in report.get("read_models") or []:
        if not row.get("present"):
            print(f"  MISSING {row['key']}")
            continue
        age = row.get("age_seconds")
        age_label = f"{float(age):.1f}s old" if age is not None else "age unknown"
        print(f"  {human_bytes(row.get('bytes')):>9} {row['key']} {age_label}")
    print()
    print("Recent Evidence")
    oom_matches = report.get("oom_evidence", {}).get("matches") or []
    if oom_matches:
        for line in oom_matches[-5:]:
            print(f"  OOM {line}")
    else:
        print("  OOM none")
    busy = report.get("server_busy", {})
    print(f"  server_busy_503 count={busy.get('count', 0)}")
    for line in busy.get("samples") or []:
        print(f"  503 {line}")


def json_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        **report,
        "checks": [check.__dict__ for check in report["checks"]],
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def age_seconds(value: Any) -> float | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def human_bytes(value: Any) -> str:
    size = float(_int(value))
    units = ["B", "KiB", "MiB", "GiB"]
    for unit in units:
        if abs(size) < 1024.0 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{int(size)} B"


def _pct(numerator: Any, denominator: Any) -> float:
    denominator_int = _int(denominator)
    if denominator_int <= 0:
        return 0.0
    return 100.0 * float(_int(numerator)) / float(denominator_int)


def _int(value: Any) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
