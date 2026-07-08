"""The agent BFF's own session + CSRF/DNS-rebinding guard — a same-shaped
but independent reimplementation of cloud.api.deps's verify_csrf/
get_current_user (not an import of them: the import-linter boundary forbids
agent -> cloud). Needed because, unlike the cloud tier (only reachable via a
real network boundary + TLS), this app binds loopback — any locally-open
webpage's JS can attempt a request against it (classic CSRF-against-
localhost / DNS-rebinding), so it needs its own Origin/Host + CSRF checks
even though only one operator ever uses it. See plan.md §13 / §19.1 Phase 9
Part 2, 2.3."""

import secrets
from fastapi import HTTPException, Request, Response

from .. import identity

_SESSION_COOKIE = "session"
_CSRF_COOKIE = "csrf"
_TRUSTED_HOSTNAMES = {"127.0.0.1", "localhost", "::1", "[::1]"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def require_loopback(request: Request) -> None:
    """The floor every BFF route sits on, regardless of what `bff_host` this
    process happens to be bound to (even 0.0.0.0) — checks the PEER's actual
    source address, so a LAN client connecting to a non-loopback bind is
    still correctly rejected. Shared by agent/api.py's job routes and every
    route this app defines, replacing what used to be a private duplicate
    inside agent/api.py."""
    host = request.client.host if request.client else None
    if host not in _TRUSTED_HOSTNAMES:
        raise HTTPException(status_code=403, detail="This endpoint is only reachable from localhost")


def _hostname_from_host_header(host_header: str) -> str:
    # Strip a trailing ":port" without breaking IPv6 "[::1]:port".
    if host_header.startswith("["):
        return host_header.split("]")[0] + "]"
    return host_header.split(":")[0]


def set_local_session_cookies(response: Response) -> None:
    response.set_cookie(_SESSION_COOKIE, "1", httponly=True, secure=False, samesite="strict")
    response.set_cookie(_CSRF_COOKIE, secrets.token_urlsafe(32), httponly=False, secure=False, samesite="strict")


def clear_local_session_cookies(response: Response) -> None:
    response.delete_cookie(_SESSION_COOKIE)
    response.delete_cookie(_CSRF_COOKIE)


class LocalRedirectException(Exception):
    """Raised by page routes (agent/bff/pages.py) to bounce an unauthenticated
    browser request to /login — mirrors cloud.api.deps.RedirectException."""

    def __init__(self, location: str = "/login"):
        self.location = location


def require_local_session(request: Request) -> None:
    """API-route guard: 401 (not a redirect) if nobody is logged in via the
    launcher yet. Page routes use current_operator_or_redirect instead."""
    if not (identity.has_session() and request.cookies.get(_SESSION_COOKIE)):
        raise HTTPException(status_code=401, detail="Not logged in — start the launcher and sign in first")


def current_operator_or_redirect(request: Request) -> identity.OperatorContext:
    try:
        require_local_session(request)
    except HTTPException:
        raise LocalRedirectException("/login")
    return identity.get_operator_context()


def verify_trusted_host(request: Request) -> None:
    """DNS-rebinding guard on its own — usable standalone on routes that
    can't carry a CSRF cookie yet (namely /auth/login: there's no session to
    protect before the first successful login, exactly like cloud/api/auth.py's
    own /auth/login being CSRF-exempt; but a forged Host header is still
    worth rejecting even there)."""
    if request.method in _SAFE_METHODS:
        return
    host_header = request.headers.get("host", "")
    if _hostname_from_host_header(host_header) not in _TRUSTED_HOSTNAMES:
        raise HTTPException(status_code=403, detail="Untrusted Host header")


def verify_local_csrf(request: Request) -> None:
    """Double-submit CSRF check + Host validation for mutating requests —
    blocks both a hostile page's cross-origin POST and a DNS-rebinding
    attempt (an attacker domain that resolves to 127.0.0.1 but presents its
    own Host header)."""
    if request.method in _SAFE_METHODS:
        return
    verify_trusted_host(request)
    cookie_token = request.cookies.get(_CSRF_COOKIE)
    header_token = request.headers.get("X-CSRF-Token")
    if not cookie_token or not header_token or cookie_token != header_token:
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token")
