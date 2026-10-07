"""Endpoint helpers for shared request limits; forwarded headers are ignored."""

import ipaddress

from fastapi import HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from codehound.db.rate_limits import (
    RequestLimitConfiguration,
    RequestLimitStorageFull,
    RequestLimitStore,
)


def _require_limit(request, bucket, identity):
    try:
        decision = RequestLimitStore(request.app.state.database).consume(bucket, identity)
    except (RequestLimitConfiguration, RequestLimitStorageFull, SQLAlchemyError):
        raise HTTPException(503, "Request rate limiting is unavailable. Try again later.") from None
    if not decision.allowed:
        raise HTTPException(
            429,
            "Too many requests. Try again after the request window resets.",
            headers={"Retry-After": str(decision.retry_after)},
        )


def require_mutation_limit(request: Request, owner_id: int):
    if type(owner_id) is not int or owner_id <= 0:
        raise HTTPException(401, "Sign in again to continue.")
    _require_limit(request, "mutation", f"owner:{owner_id}")


def remote_identity(request: Request):
    # ASGI server/proxy forwarding options must also be configured securely.
    # Application code never reads X-Forwarded-For or Forwarded for this identity.
    host = request.client.host if request.client else ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "ip:unknown"
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return f"ip:{address}"


def require_oauth_limit(request: Request):
    _require_limit(request, "oauth", remote_identity(request))
