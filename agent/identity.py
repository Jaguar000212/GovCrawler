"""The operator's own standing session — set once by the launcher at login,
used by agent/api.py's job routes to build a self-refreshing token for
long-running crawls, decoupled from whatever token authenticated any one
browser request. Zero cloud.* imports (plan.md §19.1 Phase 9): only httpx
and keyring, both already a dependency of the launcher's own login flow.
"""

import asyncio
import logging

import httpx
import keyring

log = logging.getLogger(__name__)

_KEYRING_SERVICE = "govcrawler"

_state = {"email": None, "access_token": None, "base_url": None}
_lock = asyncio.Lock()


def set_session(email: str, access_token: str, base_url: str) -> None:
    _state.update(email=email, access_token=access_token, base_url=base_url)


def clear_session() -> None:
    _state.update(email=None, access_token=None, base_url=None)


def has_session() -> bool:
    return _state["access_token"] is not None


def update_access_token(access_token: str) -> None:
    """Called by the launcher after its OWN (sync, Tk-thread) refresh cycle
    succeeds, so this module's cache doesn't go stale and trigger a second,
    avoidable refresh moments later. Doesn't fully eliminate the (rare, low-
    probability) case where both refresh concurrently against the same
    single-use rotating refresh token — a collision there just fails one
    side's refresh, which surfaces as a warning and one delayed heartbeat,
    not data loss (writes stay safely queued in the local outbox)."""
    if _state["access_token"] is not None:
        _state["access_token"] = access_token


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
    outlives any one browser request or Tk-thread call."""
    async with _lock:
        email, base_url = _state["email"], _state["base_url"]
        if not email or not base_url:
            raise RuntimeError("No operator session — the launcher hasn't logged in yet")
        refresh_token = keyring.get_password(_KEYRING_SERVICE, email)
        if not refresh_token:
            raise RuntimeError("No refresh token in keyring — operator must log in again")
        async with httpx.AsyncClient(base_url=base_url, timeout=10) as http:
            r = await http.post("/auth/refresh", json={"refresh_token": refresh_token})
            r.raise_for_status()
            data = r.json()
        _state["access_token"] = data["access_token"]
        keyring.set_password(_KEYRING_SERVICE, email, data["refresh_token"])
        log.info(f"Refreshed operator access token for {email}")
        return _state["access_token"]
