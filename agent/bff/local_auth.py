"""The agent BFF's own tiny auth surface. The Tkinter launcher is the primary
authenticator (it logs in directly against the cloud and hands the browser a
ready-made session via /local-bootstrap — see agent/launcher/app.py), but
/auth/login is also implemented here as a straight relay to the cloud so the
existing frontend/login.html + login.js (unchanged, relative fetch("/auth/
login")) keep working if the operator ever lands on that page directly (e.g.
a stale/cleared session). See .docs/authentication.md."""

import httpx
import keyring
import logging
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse

from . import security
from .. import identity, localdb

log = logging.getLogger(__name__)

_KEYRING_SERVICE = "govcrawler"

router = APIRouter(tags=["local-auth"], dependencies=[Depends(security.require_loopback)])


def _cloud_base_url() -> str:
    url = localdb.get_setting("cloud_api_base_url")
    if not url:
        raise HTTPException(status_code=500, detail="No cloud server configured — set it in the launcher first")
    return url


@router.post("/auth/login", dependencies=[Depends(security.verify_trusted_host)])
async def login(body: dict, response: Response):
    """Straight relay: forwards {email, password} to the cloud's real
    /auth/login, and on success seeds both this app's identity cache (so
    proxied/job-lifecycle calls work immediately) and the browser's local
    session cookies — the real bearer token itself never reaches the
    browser, only the local session/csrf marker pair does."""
    cloud_url = _cloud_base_url()
    async with httpx.AsyncClient(base_url=cloud_url, timeout=15) as http:
        r = await http.post("/auth/login", json=body)
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    data = r.json()
    user = data["user"]

    keyring.set_password(_KEYRING_SERVICE, user["email"], data["refresh_token"])
    identity.set_session(
        user["email"], data["access_token"], cloud_url, permissions=user["permissions"], is_admin=user["is_admin"]
    )
    security.set_local_session_cookies(response)
    return data


@router.get("/local-bootstrap")
async def local_bootstrap():
    """The launcher's `open_browser()` hits this right after its own
    successful login instead of the old cross-process /auth/bootstrap?token=
    hand-off — there's only one process now, so this just needs to prove the
    request came from loopback (already enforced at the router level) and
    that the launcher has actually logged in, then hand the browser its own
    local session marker."""
    if not identity.has_session():
        raise HTTPException(status_code=401, detail="Not logged in — sign in via the launcher first")
    response = RedirectResponse(url="/")
    security.set_local_session_cookies(response)
    return response


@router.post("/auth/logout", dependencies=[Depends(security.verify_local_csrf)])
async def logout(response: Response):
    await identity.logout()
    security.clear_local_session_cookies(response)
    return {"message": "Logged out"}


@router.get("/auth/me", dependencies=[Depends(security.require_local_session)])
async def me():
    ctx = identity.get_operator_context()
    return {"email": ctx.email, "is_admin": ctx.is_admin, "permissions": sorted(ctx.permissions)}
