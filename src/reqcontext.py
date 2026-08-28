"""Per-request authentication context.

The stdio server serves exactly one user, so a process-wide GARAGE61_TOKEN was
fine. Over HTTP every request may belong to a different user, so the token
travels in a contextvar set by the auth middleware and read wherever a Garage61
client is created. Async-safe: each request task sees only its own value.
"""

import hashlib
from contextvars import ContextVar
from typing import Optional

_request_token: ContextVar[Optional[str]] = ContextVar("garage61_token", default=None)


def set_request_token(token: Optional[str]):
    """Bind the Garage61 token for the current request. Returns a reset token."""
    return _request_token.set(token)


def reset_request_token(reset) -> None:
    _request_token.reset(reset)


def get_request_token() -> Optional[str]:
    return _request_token.get()


def user_scope() -> str:
    """Short stable identifier for the current user, safe to use in cache keys.

    Derived from the token, never stored alongside data that would identify it.
    Falls back to a fixed scope for the single-user stdio server.
    """
    token = _request_token.get()
    if not token:
        return "local"
    return hashlib.sha256(token.encode()).hexdigest()[:16]
