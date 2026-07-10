"""The operator's own standing session — set once by the launcher at login,
used by agent/api.py's job routes to build a self-refreshing token for
long-running crawls, decoupled from whatever token authenticated any one
browser request. Zero cloud.* imports (plan.md §19.1 Phase 9): only httpx
and keyring, both already a dependency of the launcher's own login flow.
"""

import asyncio
import httpx
import keyring
import logging
from keyring.errors import PasswordDeleteError

log = logging.getLogger(__name__)

_KEYRING_SERVICE = "govcrawler"

_state = {
    "email": None,
    "access_token": None,
    "base_url": None,
    "permissions": frozenset(),
    "is_admin": False,
}
_lock = asyncio.Lock()


class SessionExpiredError(Exception):
    """Raised by refresh() when the cloud rejects the refresh token itself —
    expired, or revoked server-side (a password reset or deactivation calls
    revoke_session_family, see cloud/api/auth.py). Distinct from a plain
    httpx.HTTPStatusError so every caller (agent/api.py's job routes,
    agent/bff/proxy.py's generic proxy, local_auth.py, local_system.py) can
    catch one exception type and surface a clean "log in again" instead of
    each hand-rolling status-code parsing, or worse, letting it bubble up
    as a raw 500."""


class OperatorContext:
    """Duck-typed to match cloud.api.deps.CurrentUser's `.can()` method, so
    the same Jinja templates (`{% if user.can(...) %}`) render unchanged
    whether `user` is a real CurrentUser (cloud-side) or this agent-local
    stand-in built from identity.py's cached login/refresh response."""

    def __init__(self, email: str, is_admin: bool, permissions: frozenset[str]):
        self.email = email
        self.is_admin = is_admin
        self.permissions = permissions

    def can(self, perm: str) -> bool:
        return self.is_admin or perm in self.permissions


def set_session(
    email: str, access_token: str, base_url: str, permissions: list[str] = (), is_admin: bool = False
) -> None:
    _state.update(
        email=email, access_token=access_token, base_url=base_url, permissions=frozenset(permissions), is_admin=is_admin
    )


def clear_session() -> None:
    _state.update(email=None, access_token=None, base_url=None, permissions=frozenset(), is_admin=False)


async def logout() -> None:
    """Best-effort server-side session revocation (POSTs the keyring refresh
    token to the cloud's /auth/logout, same dual-mode it already supports for
    the browser's cookie-based logout) then always clears local state — a
    failed revoke call must never leave the operator stuck 'logged in'
    locally with no way out."""
    email, base_url = _state["email"], _state["base_url"]
    if email and base_url:
        refresh_token = keyring.get_password(_KEYRING_SERVICE, email)
        if refresh_token:
            try:
                async with httpx.AsyncClient(base_url=base_url, timeout=10) as http:
                    await http.post("/auth/logout", json={"refresh_token": refresh_token})
            except Exception as e:
                log.warning(f"Cloud logout call failed (clearing local session anyway): {e}")
        try:
            keyring.delete_password(_KEYRING_SERVICE, email)
        except PasswordDeleteError:
            pass
    clear_session()


def has_session() -> bool:
    return _state["access_token"] is not None


def get_current_refresh_token() -> str | None:
    """The refresh token backing this standing session, straight from the
    keyring. Needed so a relayed /auth/sessions call can tell the cloud which
    session is "this device" — the cloud identifies the caller's own session
    via the `refresh` cookie (see cloud/api/auth.py's _current_session_id),
    which the agent doesn't have since it holds the token in keyring instead
    of a browser cookie."""
    email = _state["email"]
    if not email:
        return None
    return keyring.get_password(_KEYRING_SERVICE, email)


def get_operator_context() -> OperatorContext:
    if not has_session():
        raise RuntimeError("No operator session — the launcher hasn't logged in yet")
    return OperatorContext(_state["email"], _state["is_admin"], _state["permissions"])


def update_access_token(access_token: str, permissions: list[str] | None = None, is_admin: bool | None = None) -> None:
    """Called by the launcher after its OWN (sync, Tk-thread) refresh cycle
    succeeds, so this module's cache doesn't go stale and trigger a second,
    avoidable refresh moments later. Doesn't fully eliminate the (rare, low-
    probability) case where both refresh concurrently against the same
    single-use rotating refresh token — a collision there just fails one
    side's refresh, which surfaces as a warning and one delayed heartbeat,
    not data loss (writes stay safely queued in the local outbox). Also
    refreshes the cached permission set — /auth/refresh's response already
    carries the user's current effective permissions for free, so a mid-
    session role change is picked up on the next refresh without an extra
    round trip."""
    if _state["access_token"] is not None:
        _state["access_token"] = access_token
        if permissions is not None:
            _state["permissions"] = frozenset(permissions)
        if is_admin is not None:
            _state["is_admin"] = is_admin


async def get_valid_token() -> str:
    """The cached access token. Callers that get a 401 should call refresh()
    once and retry — this never proactively checks expiry itself, mirroring
    the launcher's own refresh-on-401 pattern rather than inventing a second
    one based on decoding the token's exp claim."""
    if not has_session():
        raise RuntimeError("No operator session — the launcher hasn't logged in yet")
    return _state["access_token"]


async def refresh() -> str:
    """POST /auth/refresh with the keyring-stored refresh token, cache and
    return the new access token, and rotate the stored refresh token (it's
    single-use) — the same flow agent/launcher/app.py._try_refresh_sync
    already proves, just async and usable from a background crawl task that
    outlives any one browser request or Tk-thread call. The response's
    embedded `user` also carries fresh effective permissions — no separate
    /auth/me round trip needed to keep the cached permission set current.

    Raises SessionExpiredError (never a raw HTTPStatusError) whenever the
    refresh token itself is unusable — missing, or rejected by the cloud —
    so callers get one exception type to catch instead of an uncaught 401
    from /auth/refresh surfacing as a generic 500."""
    async with _lock:
        email, base_url = _state["email"], _state["base_url"]
        if not email or not base_url:
            raise SessionExpiredError("No operator session — please log in again.")
        refresh_token = keyring.get_password(_KEYRING_SERVICE, email)
        if not refresh_token:
            clear_session()
            raise SessionExpiredError("No refresh token found — please log in again.")
        async with httpx.AsyncClient(base_url=base_url, timeout=10) as http:
            r = await http.post("/auth/refresh", json={"refresh_token": refresh_token})
            if r.status_code == 401:
                clear_session()
                try:
                    keyring.delete_password(_KEYRING_SERVICE, email)
                except PasswordDeleteError:
                    pass
                raise SessionExpiredError("Your session has expired — please log in again.")
            r.raise_for_status()
            data = r.json()
        _state["access_token"] = data["access_token"]
        _state["permissions"] = frozenset(data["user"]["permissions"])
        _state["is_admin"] = data["user"]["is_admin"]
        keyring.set_password(_KEYRING_SERVICE, email, data["refresh_token"])
        log.info(f"Refreshed operator access token for {email}")
        return _state["access_token"]
