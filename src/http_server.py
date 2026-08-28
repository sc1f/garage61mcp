"""HTTP (streamable) entry point for the Garage61 MCP server.

Runs the same Server as the stdio entry point behind Starlette + uvicorn, with
bring-your-own-key auth: every request carries the caller's Garage61 personal
access token as `Authorization: Bearer <token>`. The token is verified upstream
once per TTL, then bound to the request context so all Garage61 calls act as
that user.

Environment:
    HOST / PORT                     bind address (default 127.0.0.1:8080)
    GARAGE61_MCP_ALLOWED_HOSTS      comma list enabling DNS-rebinding protection
    GARAGE61_MCP_ACCESS_KEY         if set, every request must also carry it as
                                    X-MCP-Access-Key -- makes the endpoint
                                    effectively private (strangers cost nothing)

Credentials travel as headers. The Garage61 token goes in `Authorization:
Bearer <token>` or in `X-Garage61-Token: <token>`. The second name exists
because a client that also supports OAuth can reserve the `Authorization`
header for its own use.

Query parameters (?key=...&token=...) are also accepted, for clients that can
send neither header. That path puts credentials in the URL, thus the access log
stays off.
    GARAGE61_LOG_LEVEL              WARNING by default
"""

import contextlib
import hmac
import logging
import os
import sys
import time

# Bare-name imports (see __main__.py): make the source dir importable no matter
# how this module is launched (uvicorn src.http_server, python src/http_server.py...).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from urllib.parse import parse_qs

from starlette.routing import Route

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from reqcontext import set_request_token, reset_request_token
from server import build_server

logger = logging.getLogger(__name__)

GARAGE61_API = os.getenv("GARAGE61_BASE_URL", "https://garage61.net/api/v1")

# Token verification results, keyed by the raw token, kept briefly so each
# request does not cost an extra upstream round-trip. Failures are cached too,
# so a bad token cannot be used to hammer Garage61 through us.
# Requests larger than this are rejected before the body is read. MCP tool
# calls are a few hundred bytes; anything bigger is not a legitimate client.
_MAX_BODY_BYTES = 256 * 1024

# Upstream verification limiter: every *unique* unknown token costs one
# Garage61 /me call, so a flood of random tokens would otherwise turn this
# server into an amplifier pointed at their API. Token bucket: small burst,
# steady refill; beyond it, unknown tokens are rejected without any upstream
# traffic (known-good tokens ride the verify cache and are unaffected).
_VERIFY_BURST = 10.0
_VERIFY_REFILL_PER_S = 1.0
_verify_bucket = {"level": _VERIFY_BURST, "at": time.monotonic()}


def _verification_allowed() -> bool:
    now = time.monotonic()
    b = _verify_bucket
    b["level"] = min(_VERIFY_BURST, b["level"] + (now - b["at"]) * _VERIFY_REFILL_PER_S)
    b["at"] = now
    if b["level"] < 1.0:
        return False
    b["level"] -= 1.0
    return True


_VERIFY_TTL_S = 600
_VERIFY_FAIL_TTL_S = 60
_verify_cache: dict[str, tuple[bool, float]] = {}
_VERIFY_CACHE_MAX = 10_000


async def _verify_token(token: str) -> bool:
    """Check a Garage61 token by asking /me, with positive and negative caching."""
    now = time.monotonic()
    hit = _verify_cache.get(token)
    if hit and hit[1] > now:
        return hit[0]

    if not _verification_allowed():
        # Do not cache: a legitimate new user arriving during a flood should
        # succeed on retry once the bucket refills.
        logger.warning("Token verification rate limit hit; rejecting without upstream call")
        return False

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{GARAGE61_API}/me",
                headers={"Authorization": f"Bearer {token}"},
            )
        ok = response.status_code == 200
    except httpx.RequestError as e:
        # Upstream unreachable is not the caller's fault; let the request in and
        # let the tool surface the real error. Do not cache.
        logger.warning(f"Token verification skipped, Garage61 unreachable: {e}")
        return True

    if len(_verify_cache) > _VERIFY_CACHE_MAX:
        _verify_cache.clear()
    _verify_cache[token] = (ok, now + (_VERIFY_TTL_S if ok else _VERIFY_FAIL_TTL_S))
    return ok


def _unauthorized(detail: str) -> Response:
    return JSONResponse(
        {"error": "unauthorized", "detail": detail},
        status_code=401,
        headers={"WWW-Authenticate": 'Bearer resource="garage61-mcp"'},
    )


class BearerAuthMiddleware:
    """Require a valid Garage61 token and bind it to the request context."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        query = parse_qs(scope.get("query_string", b"").decode())

        # Optional private mode: with an access key configured, requests
        # without it are rejected before any work happens. Constant-time
        # comparison; the key is a gate, not a user identity. The query-param
        # form exists for claude.ai connectors (mobile), which cannot send
        # custom headers.
        access_key = os.getenv("GARAGE61_MCP_ACCESS_KEY", "")
        if access_key:
            supplied = headers.get("x-mcp-access-key", "") or (query.get("key") or [""])[0]
            if not hmac.compare_digest(supplied, access_key):
                response = _unauthorized("Missing or invalid X-MCP-Access-Key.")
                await response(scope, receive, send)
                return

        # Reject oversized bodies before reading them.
        try:
            content_length = int(headers.get("content-length", "0"))
        except ValueError:
            content_length = _MAX_BODY_BYTES + 1
        if content_length > _MAX_BODY_BYTES:
            response = JSONResponse(
                {"error": "payload_too_large"}, status_code=413
            )
            await response(scope, receive, send)
            return

        # The Garage61 token, in order of preference. `X-Garage61-Token` exists
        # because a client that also does OAuth can reserve or replace the
        # `Authorization` header; a private header name cannot collide with it.
        auth = headers.get("authorization", "")
        alt_token = headers.get("x-garage61-token", "").strip()
        query_token = (query.get("token") or [""])[0].strip()
        if not auth:
            fallback = alt_token or query_token
            if fallback:
                auth = f"Bearer {fallback}"
        if not auth.lower().startswith("bearer ") or not auth[7:].strip():
            response = _unauthorized(
                "Provide your Garage61 personal access token. Use the header "
                "'Authorization: Bearer <token>' or the header "
                "'X-Garage61-Token: <token>'. Create a token at "
                "https://garage61.net (Account -> API)."
            )
            await response(scope, receive, send)
            return

        token = auth[7:].strip()
        if not await _verify_token(token):
            response = _unauthorized("Garage61 rejected this token.")
            await response(scope, receive, send)
            return

        bound = set_request_token(token)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_request_token(bound)


def _security_settings() -> TransportSecuritySettings | None:
    hosts = [h.strip() for h in os.getenv("GARAGE61_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    if not hosts:
        return None  # SDK default: DNS-rebinding protection off (fine behind TLS proxy)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=[]
    )


def create_app() -> Starlette:
    server = build_server()

    # Stateless: no session affinity needed, so any number of instances behind a
    # load balancer work, and a restart loses nothing the client cannot redo.
    # json_response: plain JSON replies instead of SSE streams -- none of our
    # tools emit progress notifications, and JSON survives every proxy.
    session_manager = StreamableHTTPSessionManager(
        app=server,
        stateless=True,
        json_response=True,
        security_settings=_security_settings(),
    )

    async def handle_mcp(scope, receive, send):
        await session_manager.handle_request(scope, receive, send)

    async def healthz(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with session_manager.run():
            logger.info("Garage61 MCP HTTP server ready")
            yield

    # A Route with an ASGI endpoint (class instance, not a function) serves the
    # exact /mcp path directly; Mount would 307-redirect bare /mcp to /mcp/,
    # which not every MCP client follows.
    mcp_endpoint = BearerAuthMiddleware(handle_mcp)
    return Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/mcp", endpoint=mcp_endpoint, methods=["GET", "POST", "DELETE"]),
        ],
        lifespan=lifespan,
    )


app = create_app()


def main() -> None:
    import uvicorn

    # Unlike the stdio entry point (where stderr chatter gets in the way and
    # WARNING is right), an HTTP server should say where it is listening and
    # log each request. DEBUG still dumps telemetry payloads -- opt-in only.
    level = os.getenv("GARAGE61_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.WARNING),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )
    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8080")),
        log_level=level.lower(),
    )


if __name__ == "__main__":
    main()
