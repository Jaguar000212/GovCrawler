"""Generic reverse proxy for every shared-data router the cloud still owns —
domains, leads, campaigns, templates, credentials, blacklist, imports,
config, and read-only job lookups. One handler, not ~15 hand-written
pass-throughs, built on the same retry-on-401-then-refresh helper
agent/cloud_client.py already has (agent/cloud_client.py:request_with_retry).
Mounted LAST in agent/bff/app.py so the more specific routers registered
before it (agent/api.py's job-lifecycle routes, local_auth.py, local_system.py)
win on any path overlap — FastAPI matches path+method in registration order.
See plan.md §19.1 Phase 9 Part 2, 2.4."""

import logging
import httpx
from fastapi import APIRouter, Depends, Request, Response

from . import security
from .. import identity
from ..cloud_client import request_with_retry
from .local_auth import _cloud_base_url

log = logging.getLogger(__name__)

router = APIRouter(
    tags=["proxy"],
    dependencies=[
        Depends(security.require_loopback),
        Depends(security.require_local_session),
        Depends(security.verify_local_csrf),
    ],
)

# Headers that must never be blindly relayed either direction: the browser's
# local session/csrf cookies never leave this machine, `authorization` is set
# fresh from identity.py (not whatever the browser sent, which is nothing —
# the browser never holds the real token), and the length/encoding headers
# are recomputed by httpx from the actual bytes being sent/returned.
_STRIP_REQUEST_HEADERS = {"host", "authorization", "cookie", "content-length"}
_STRIP_RESPONSE_HEADERS = {"content-length", "transfer-encoding", "connection", "content-encoding"}


@router.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request) -> Response:
    cloud_url = _cloud_base_url()
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _STRIP_REQUEST_HEADERS}
    params = list(request.query_params.multi_items())

    async with httpx.AsyncClient(base_url=cloud_url, timeout=30) as http:
        r = await request_with_retry(
            request.method, http, f"/api/{path}", identity.get_valid_token, identity.refresh,
            params=params, content=body, headers=headers,
        )

    response_headers = {k: v for k, v in r.headers.items() if k.lower() not in _STRIP_RESPONSE_HEADERS}
    return Response(content=r.content, status_code=r.status_code, headers=response_headers,
                    media_type=r.headers.get("content-type"))
