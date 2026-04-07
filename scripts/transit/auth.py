"""
Auth and RBAC scaffolding for Transit Sentinel API.

Provides:
  - Bearer token verification (env-configured, multi-token support)
  - Role-based access control (roles: viewer, operator, admin)
  - A simple audit-trail writer to Valkey

Token configuration
-------------------
Tokens are configured via the TRANSIT_API_TOKENS environment variable as a
comma-separated list of ``token:role`` pairs, e.g.:

    TRANSIT_API_TOKENS=secret123:operator,readonly456:viewer,admin789:admin

Each token maps to exactly one role. If TRANSIT_API_TOKENS is not set, auth
is disabled and all requests are treated as the "viewer" role — this preserves
the existing open-access behaviour and lets the system work without config.

To require auth, set TRANSIT_API_REQUIRE_AUTH=1. When set, requests without
a valid bearer token are rejected with 401.

Roles
-----
viewer   — read-only access to all GET endpoints
operator — viewer + can acknowledge incidents (POST /api/transit/incidents/ack)
admin    — operator + full access including trace/replay management

Public status endpoints (/api/status/*) are always open regardless of auth.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Role constants
# ---------------------------------------------------------------------------

ROLE_VIEWER = "viewer"
ROLE_OPERATOR = "operator"
ROLE_ADMIN = "admin"

_ROLE_RANK = {
    ROLE_VIEWER: 0,
    ROLE_OPERATOR: 1,
    ROLE_ADMIN: 2,
}


def role_can(role: str, required_role: str) -> bool:
    """Return True if *role* satisfies the *required_role* requirement."""
    return _ROLE_RANK.get(role, -1) >= _ROLE_RANK.get(required_role, 999)


# ---------------------------------------------------------------------------
# Token registry
# ---------------------------------------------------------------------------


class TokenRegistry:
    """Holds the configured token→role mapping."""

    def __init__(self) -> None:
        self._tokens: Dict[str, str] = {}
        self._load_env()

    def _load_env(self) -> None:
        raw = os.getenv("TRANSIT_API_TOKENS", "").strip()
        if not raw:
            return
        for entry in raw.split(","):
            entry = entry.strip()
            if ":" not in entry:
                continue
            token, role = entry.split(":", 1)
            token = token.strip()
            role = role.strip().lower()
            if token and role in _ROLE_RANK:
                self._tokens[token] = role

    def resolve(self, token: str) -> Optional[str]:
        """Return the role for *token*, or None if not recognised."""
        return self._tokens.get(token)

    @property
    def enabled(self) -> bool:
        """True if any tokens are configured."""
        return bool(self._tokens)


# Module-level registry (loaded once at import time)
_registry = TokenRegistry()

# Whether to hard-require auth (reject unauthenticated requests)
_require_auth = os.getenv("TRANSIT_API_REQUIRE_AUTH", "").strip() in (
    "1",
    "true",
    "yes",
)


def reload_registry() -> None:
    """Reload token registry from environment (useful in tests)."""
    global _registry
    _registry = TokenRegistry()


# ---------------------------------------------------------------------------
# Request-level auth resolution
# ---------------------------------------------------------------------------


def resolve_auth(
    authorization_header: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """Parse an Authorization header and return (token, role).

    Returns (None, None) if the header is absent or malformed.
    Returns (token, None) if the token is present but not recognised.
    Returns (token, role) if the token is valid and mapped to a role.
    """
    if not authorization_header:
        return None, None
    parts = authorization_header.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None, None
    token = parts[1].strip()
    role = _registry.resolve(token)
    return token, role


def check_auth(
    authorization_header: Optional[str],
    *,
    required_role: str = ROLE_VIEWER,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Check if the request is authorised.

    Returns (authorised, token, role).

    If auth is disabled (no tokens configured, TRANSIT_API_REQUIRE_AUTH not
    set) or the endpoint is a public endpoint, returns (True, None, 'viewer').
    """
    # If auth is completely disabled, pass through with viewer role
    if not _registry.enabled and not _require_auth:
        return True, None, ROLE_VIEWER

    token, role = resolve_auth(authorization_header)

    if not token:
        # No token presented
        if _require_auth:
            return False, None, None
        # Auth is optional but tokens exist; grant viewer without token
        return True, None, ROLE_VIEWER

    if role is None:
        # Token not in registry
        return False, token, None

    if not role_can(role, required_role):
        return False, token, role

    return True, token, role


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

_AUDIT_KEY = "transit:audit:trail"
_AUDIT_MAX = 1000  # Rolling window of audit entries


def write_audit_event(
    client: Any,
    *,
    action: str,
    token: Optional[str] = None,
    role: Optional[str] = None,
    resource: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    """Write a single audit event to the Valkey audit trail list.

    Silently no-ops if writing fails (audit should not break the main path).
    """
    try:
        event = json.dumps(
            {
                "timestamp_ms": int(time.time() * 1000),
                "action": action,
                "token_prefix": token[:8] + "…" if token and len(token) > 8 else token,
                "role": role,
                "resource": resource,
                "payload": payload or {},
            },
            default=str,
        )
        client.rpush(_AUDIT_KEY, event)
        # Trim to rolling window
        client.ltrim(_AUDIT_KEY, -_AUDIT_MAX, -1)
    except Exception:  # pragma: no cover
        pass


def read_audit_trail(client: Any, *, limit: int = 100) -> list:
    """Read the most recent *limit* audit events from Valkey."""
    try:
        raw_entries = client.lrange(_AUDIT_KEY, -limit, -1) or []
        events = []
        for raw in raw_entries:
            try:
                events.append(json.loads(raw))
            except json.JSONDecodeError:
                pass
        return list(reversed(events))
    except Exception:  # pragma: no cover
        return []
