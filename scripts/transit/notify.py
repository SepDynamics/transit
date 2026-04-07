#!/usr/bin/env python3
"""Transit Sentinel notification dispatcher.

Dispatches incident notifications to configured webhook, email (SMTP), and
log-file targets when new corridor incidents are detected.

Usage:
    # Run as a sidecar that polls the API and fires on new incidents:
    python scripts/transit/notify.py --api http://localhost:8000 --webhook https://hooks.example.com/transit

    # With email:
    python scripts/transit/notify.py \\
        --api http://localhost:8000 \\
        --smtp-host smtp.example.com \\
        --smtp-user alerts@example.com \\
        --smtp-password secret \\
        --email-from alerts@example.com \\
        --email-to ops@example.com

Environment variable equivalents (all optional):
    TRANSIT_API_URL             Base URL for the Transit Sentinel API
    TRANSIT_NOTIFY_WEBHOOK_URL  Webhook POST target (Slack, Teams, custom)
    TRANSIT_NOTIFY_SCOPE        Feed scope to monitor (default: live)
    TRANSIT_NOTIFY_INTERVAL     Poll interval in seconds (default: 10)
    TRANSIT_NOTIFY_MIN_SEVERITY Minimum incident severity to fire (default: warning)
    TRANSIT_NOTIFY_PROOF_OUTPUT_ROOT  Persist proof windows for new incidents
    TRANSIT_NOTIFY_PROOF_ARCHIVE_ROOT Override archive root for proof capture
    SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD
    NOTIFY_EMAIL_FROM / NOTIFY_EMAIL_TO
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import smtplib
import sys
import time
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib import request as urllib_request
from urllib.error import URLError

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.shared.runtime import isoformat_ms
from scripts.transit.agencies import get_transit_agency_adapter
from scripts.transit.proof_windows import (
    TransitProofWindowConfig,
    capture_proof_window,
)

logger = logging.getLogger("transit-notify")

# ---------------------------------------------------------------------------
# Severity ordering
# ---------------------------------------------------------------------------

SEVERITY_ORDER = {"critical": 3, "warning": 2, "info": 1, "low": 0}


def severity_passes(incident_severity: str, min_severity: str) -> bool:
    return SEVERITY_ORDER.get(incident_severity, 1) >= SEVERITY_ORDER.get(
        min_severity, 2
    )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class NotifyConfig:
    api_url: str = "http://localhost:8000"
    scope: str = "live"
    interval_seconds: float = 10.0
    min_severity: str = "warning"
    webhook_url: Optional[str] = None
    webhook_headers: Dict[str, str] = field(default_factory=dict)
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: bool = True
    email_from: Optional[str] = None
    email_to: List[str] = field(default_factory=list)
    log_file: Optional[str] = None
    dedup_window_seconds: float = 300.0  # suppress repeat fires within 5 min
    proof_archive_root: Optional[str] = None
    proof_output_root: Optional[str] = None
    proof_before_minutes: int = 60
    proof_after_minutes: int = 60


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


@dataclass
class _DedupeState:
    seen: Dict[str, float] = field(default_factory=dict)

    def is_duplicate(self, incident_id: str, now: float, window_seconds: float) -> bool:
        last_seen = self.seen.get(incident_id)
        if last_seen is not None and (now - last_seen) < window_seconds:
            return True
        self.seen[incident_id] = now
        return False

    def purge_stale(self, now: float, window_seconds: float) -> None:
        stale = [k for k, v in self.seen.items() if (now - v) > window_seconds * 2]
        for k in stale:
            del self.seen[k]


# ---------------------------------------------------------------------------
# Notification formatters
# ---------------------------------------------------------------------------


def _format_incident_text(incident: Dict[str, Any]) -> str:
    action = str(
        incident.get("action") or incident.get("recommended_action") or "monitor"
    ).replace("_", " ")
    regime = str(incident.get("regime") or "").replace("_", " ")
    hazard = incident.get("hazard") or incident.get("hazard_score") or 0.0
    summary = str(incident.get("summary") or "")
    label = str(incident.get("label") or incident.get("entity_id") or "")
    severity = str(incident.get("severity") or "warning").upper()
    return (
        f"[{severity}] {label} | {regime} | hazard {hazard:.2f}\n"
        f"Action: {action}\n"
        f"{summary}"
    ).strip()


def _format_webhook_payload(
    incident: Dict[str, Any], agency: str = ""
) -> Dict[str, Any]:
    """Build a Slack-compatible webhook payload (also works with Teams / generic JSON hooks)."""
    action = str(
        incident.get("action") or incident.get("recommended_action") or "monitor"
    ).replace("_", " ")
    regime = str(incident.get("regime") or "").replace("_", " ")
    hazard = float(incident.get("hazard") or incident.get("hazard_score") or 0.0)
    label = str(incident.get("label") or incident.get("entity_id") or "")
    severity = str(incident.get("severity") or "warning")
    color = (
        "#ef4444"
        if severity == "critical"
        else "#f59e0b"
        if severity == "warning"
        else "#3b82f6"
    )
    return {
        "text": f"Transit Sentinel: {label} — {regime}",
        "attachments": [
            {
                "color": color,
                "title": f"{label} — {regime.title()}",
                "text": str(incident.get("summary") or ""),
                "fields": [
                    {"title": "Action", "value": action, "short": True},
                    {"title": "Hazard", "value": f"{hazard:.2f}", "short": True},
                    {"title": "Severity", "value": severity.title(), "short": True},
                    {"title": "Agency", "value": agency or "n/a", "short": True},
                ],
                "footer": f"Transit Sentinel | {isoformat_ms()}",
            }
        ],
        "incident": incident,
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class NotificationDispatcher:
    def __init__(self, config: NotifyConfig) -> None:
        self.cfg = config
        self._dedupe = _DedupeState()
        self._log_handle = None
        if config.log_file:
            self._log_handle = open(config.log_file, "a", encoding="utf-8")  # noqa: SIM115

    def dispatch(
        self,
        incident: Dict[str, Any],
        *,
        agency: str = "",
        agency_key: str = "",
    ) -> None:
        incident_id = str(
            incident.get("incident_id") or incident.get("entity_id") or "unknown"
        )
        severity = str(incident.get("severity") or "warning")
        now = time.time()
        if not severity_passes(severity, self.cfg.min_severity):
            return
        if self._dedupe.is_duplicate(incident_id, now, self.cfg.dedup_window_seconds):
            logger.debug(
                "suppressing duplicate notification for incident %s", incident_id
            )
            return
        self._dedupe.purge_stale(now, self.cfg.dedup_window_seconds)
        logger.info(
            "dispatching notification for incident %s (%s)", incident_id, severity
        )
        if self.cfg.webhook_url:
            self._send_webhook(incident, agency)
        if self.cfg.smtp_host and self.cfg.email_to:
            self._send_email(incident, agency)
        if self._log_handle:
            self._write_log(incident, agency)
        if self.cfg.proof_output_root:
            self._capture_proof_window(incident, agency_key=agency_key)

    def _send_webhook(self, incident: Dict[str, Any], agency: str) -> None:
        payload = _format_webhook_payload(incident, agency)
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", **self.cfg.webhook_headers}
        req = urllib_request.Request(
            url=self.cfg.webhook_url,  # type: ignore[arg-type]
            data=body,
            method="POST",
            headers=headers,
        )
        try:
            with urllib_request.urlopen(req, timeout=10) as resp:
                status = resp.status
                if status not in (200, 201, 202, 204):
                    logger.warning(
                        "webhook returned unexpected status %s for incident %s",
                        status,
                        incident.get("incident_id"),
                    )
                else:
                    logger.info(
                        "webhook dispatched OK for incident %s",
                        incident.get("incident_id"),
                    )
        except URLError as exc:
            logger.warning("webhook dispatch failed: %s", exc)

    def _send_email(self, incident: Dict[str, Any], agency: str) -> None:
        label = str(incident.get("label") or incident.get("entity_id") or "")
        regime = str(incident.get("regime") or "").replace("_", " ")
        severity = str(incident.get("severity") or "warning").upper()
        subject = f"[Transit Sentinel] {severity} — {label} {regime}"
        body_text = _format_incident_text(incident)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.cfg.email_from or self.cfg.smtp_user or "sentinel@transit"
        msg["To"] = ", ".join(self.cfg.email_to)
        msg.attach(MIMEText(body_text, "plain"))
        msg.attach(MIMEText(f"<pre>{body_text}</pre>", "html"))
        try:
            if self.cfg.smtp_use_tls:
                smtp = smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port, timeout=15)  # type: ignore[arg-type]
                smtp.starttls()
            else:
                smtp = smtplib.SMTP_SSL(
                    self.cfg.smtp_host, self.cfg.smtp_port, timeout=15
                )  # type: ignore[arg-type]
            if self.cfg.smtp_user and self.cfg.smtp_password:
                smtp.login(self.cfg.smtp_user, self.cfg.smtp_password)
            smtp.sendmail(msg["From"], self.cfg.email_to, msg.as_string())
            smtp.quit()
            logger.info(
                "email dispatched to %s for incident %s",
                self.cfg.email_to,
                incident.get("incident_id"),
            )
        except Exception as exc:
            logger.warning("email dispatch failed: %s", exc)

    def _write_log(self, incident: Dict[str, Any], agency: str) -> None:
        try:
            entry = {
                "dispatched_at": isoformat_ms(),
                "agency": agency,
                "incident": incident,
            }
            self._log_handle.write(json.dumps(entry) + "\n")  # type: ignore[union-attr]
            self._log_handle.flush()  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("notification log write failed: %s", exc)

    def close(self) -> None:
        if self._log_handle:
            try:
                self._log_handle.close()
            except Exception:
                pass

    def _capture_proof_window(self, incident: Dict[str, Any], *, agency_key: str) -> None:
        resolved_agency_key = (
            str(agency_key or incident.get("agency_key") or "").strip().lower()
        )
        archive_root = str(self.cfg.proof_archive_root or "").strip()
        if not archive_root and resolved_agency_key:
            try:
                archive_root = str(
                    get_transit_agency_adapter(resolved_agency_key).archive_root_path()
                )
            except Exception:
                archive_root = ""
        if not archive_root:
            logger.warning(
                "proof-window capture skipped for incident %s: no archive root configured",
                incident.get("incident_id") or incident.get("entity_id"),
            )
            return
        try:
            manifest = capture_proof_window(
                TransitProofWindowConfig(
                    archive_root=Path(archive_root),
                    output_root=Path(str(self.cfg.proof_output_root)),
                    incident=incident,
                    before_minutes=max(1, int(self.cfg.proof_before_minutes)),
                    after_minutes=max(1, int(self.cfg.proof_after_minutes)),
                    agency_key=resolved_agency_key or None,
                )
            )
            logger.info(
                "captured proof window for incident %s at %s",
                incident.get("incident_id") or incident.get("entity_id"),
                manifest.get("bundle_root"),
            )
        except Exception as exc:
            logger.warning(
                "proof-window capture failed for incident %s: %s",
                incident.get("incident_id") or incident.get("entity_id"),
                exc,
            )


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------


def _fetch_incidents(api_url: str, scope: str) -> List[Dict[str, Any]]:
    url = f"{api_url.rstrip('/')}/api/transit/incidents?scope={scope}"
    req = urllib_request.Request(
        url, method="GET", headers={"Accept": "application/json"}
    )
    try:
        with urllib_request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return list(data.get("incidents") or [])
    except Exception as exc:
        logger.warning("incident fetch failed: %s", exc)
        return []


def _fetch_health(api_url: str) -> Dict[str, Any]:
    url = f"{api_url.rstrip('/')}/api/transit/health"
    req = urllib_request.Request(
        url, method="GET", headers={"Accept": "application/json"}
    )
    try:
        with urllib_request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}


class NotifyPollingService:
    def __init__(self, config: NotifyConfig) -> None:
        self.cfg = config
        self.dispatcher = NotificationDispatcher(config)
        self._seen_incident_ids: Set[str] = set()
        self._stop = False

    def run(self) -> None:
        logger.info(
            "Transit Sentinel notify service starting: api=%s scope=%s interval=%.0fs",
            self.cfg.api_url,
            self.cfg.scope,
            self.cfg.interval_seconds,
        )
        while not self._stop:
            started = time.time()
            try:
                self._poll()
            except Exception:
                logger.exception("notification poll failed")
            elapsed = time.time() - started
            time.sleep(max(1.0, self.cfg.interval_seconds - elapsed))

    def stop(self) -> None:
        self._stop = True

    def _poll(self) -> None:
        health = _fetch_health(self.cfg.api_url)
        agency = str(health.get("system_name") or "")
        agency_key = str(
            (health.get("ingest_status") or {}).get("agency_key")
            or (health.get("ingest_status") or {}).get("feed_status", {}).get("agency_key")
            or ""
        ).strip()
        incidents = _fetch_incidents(self.cfg.api_url, self.cfg.scope)
        new_ids: Set[str] = set()
        for incident in incidents:
            incident_id = str(
                incident.get("incident_id") or incident.get("entity_id") or ""
            )
            if not incident_id:
                continue
            new_ids.add(incident_id)
            if incident_id not in self._seen_incident_ids:
                self.dispatcher.dispatch(
                    incident,
                    agency=agency,
                    agency_key=agency_key or str(incident.get("agency_key") or ""),
                )
        # Prune IDs that are no longer active
        self._seen_incident_ids = new_ids


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Transit Sentinel notification dispatcher")
    p.add_argument(
        "--api",
        default=os.getenv("TRANSIT_API_URL", "http://localhost:8000"),
        help="Transit Sentinel API base URL",
    )
    p.add_argument("--scope", default=os.getenv("TRANSIT_NOTIFY_SCOPE", "live"))
    p.add_argument(
        "--interval",
        type=float,
        default=float(os.getenv("TRANSIT_NOTIFY_INTERVAL", "10")),
    )
    p.add_argument(
        "--min-severity",
        default=os.getenv("TRANSIT_NOTIFY_MIN_SEVERITY", "warning"),
        choices=list(SEVERITY_ORDER),
    )
    p.add_argument(
        "--webhook",
        default=os.getenv("TRANSIT_NOTIFY_WEBHOOK_URL", ""),
        help="Webhook URL (Slack / Teams / custom)",
    )
    p.add_argument("--smtp-host", default=os.getenv("SMTP_HOST", ""))
    p.add_argument("--smtp-port", type=int, default=int(os.getenv("SMTP_PORT", "587")))
    p.add_argument("--smtp-user", default=os.getenv("SMTP_USER", ""))
    p.add_argument("--smtp-password", default=os.getenv("SMTP_PASSWORD", ""))
    p.add_argument("--smtp-no-tls", action="store_true")
    p.add_argument("--email-from", default=os.getenv("NOTIFY_EMAIL_FROM", ""))
    p.add_argument(
        "--email-to",
        default=os.getenv("NOTIFY_EMAIL_TO", ""),
        help="Comma-separated list of recipient addresses",
    )
    p.add_argument(
        "--log-file",
        default=os.getenv("TRANSIT_NOTIFY_LOG_FILE", ""),
        help="Append notification events to a JSONL file",
    )
    p.add_argument(
        "--dedup-window",
        type=float,
        default=float(os.getenv("TRANSIT_NOTIFY_DEDUP_WINDOW", "300")),
    )
    p.add_argument(
        "--proof-output-root",
        default=os.getenv("TRANSIT_NOTIFY_PROOF_OUTPUT_ROOT", ""),
        help="Persist archive-backed proof windows for newly detected incidents",
    )
    p.add_argument(
        "--proof-archive-root",
        default=os.getenv("TRANSIT_NOTIFY_PROOF_ARCHIVE_ROOT", ""),
        help="Override the archive root used for proof-window capture",
    )
    p.add_argument(
        "--proof-before-minutes",
        type=int,
        default=int(os.getenv("TRANSIT_NOTIFY_PROOF_BEFORE_MINUTES", "60")),
    )
    p.add_argument(
        "--proof-after-minutes",
        type=int,
        default=int(os.getenv("TRANSIT_NOTIFY_PROOF_AFTER_MINUTES", "60")),
    )
    return p


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    args = build_parser().parse_args()
    email_to = [
        addr.strip() for addr in str(args.email_to or "").split(",") if addr.strip()
    ]
    cfg = NotifyConfig(
        api_url=str(args.api or "http://localhost:8000"),
        scope=str(args.scope or "live"),
        interval_seconds=max(1.0, float(args.interval or 10)),
        min_severity=str(args.min_severity or "warning"),
        webhook_url=str(args.webhook or "").strip() or None,
        smtp_host=str(args.smtp_host or "").strip() or None,
        smtp_port=int(args.smtp_port or 587),
        smtp_user=str(args.smtp_user or "").strip() or None,
        smtp_password=str(args.smtp_password or "").strip() or None,
        smtp_use_tls=not bool(args.smtp_no_tls),
        email_from=str(args.email_from or "").strip() or None,
        email_to=email_to,
        log_file=str(args.log_file or "").strip() or None,
        dedup_window_seconds=max(5.0, float(args.dedup_window or 300)),
        proof_output_root=str(args.proof_output_root or "").strip() or None,
        proof_archive_root=str(args.proof_archive_root or "").strip() or None,
        proof_before_minutes=max(1, int(args.proof_before_minutes or 60)),
        proof_after_minutes=max(1, int(args.proof_after_minutes or 60)),
    )

    if not any([cfg.webhook_url, cfg.smtp_host, cfg.log_file]):
        logger.warning(
            "No notification targets configured. "
            "Specify at least one of --webhook, --smtp-host, or --log-file. "
            "Running in dry-run mode (incidents will be logged at INFO level)."
        )

    svc = NotifyPollingService(cfg)

    def _handle_signal(signum: int, _frame: Any) -> None:
        logger.info("received signal %s, stopping notify service", signum)
        svc.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    svc.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
