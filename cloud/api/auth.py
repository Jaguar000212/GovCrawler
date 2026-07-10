"""Authentication endpoints (login/refresh/logout/me). See .docs/authentication.md."""

import datetime
import logging
import secrets
import time
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from shared.schemas.auth import LoginRequest, RefreshRequest, TokenResponse, UserOut
from .deps import CurrentUser, client_ip, get_config, get_current_user, get_db, verify_csrf
from ..db import Database, User
from ..security.hashing import DUMMY_PASSWORD_HASH, verify_password
from ..security.jwt import create_access_token, generate_refresh_token, hash_refresh_token

log = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

# ── Login rate limiting ──────────────────────────────────────────────────────
# Per-IP sliding window over failed attempts only (mirrors the per-account
# lockout_threshold/lockout_minutes design so legitimate repeated correct
# logins are never punished). In-memory is safe here — this app always runs
# as a single process (see CLAUDE.md "Deployment reality").
_failed_login_attempts: dict[str, list[float]] = {}


def _check_login_rate_limit(ip: str | None, config: dict) -> None:
    if not ip:
        return
    auth_cfg = config["auth"]
    limit = auth_cfg.get("login_rate_limit_attempts", 20)
    window_seconds = auth_cfg.get("login_rate_limit_window_minutes", 15) * 60
    now = time.monotonic()
    attempts = [t for t in _failed_login_attempts.get(ip, []) if now - t < window_seconds]
    _failed_login_attempts[ip] = attempts
    if len(attempts) >= limit:
        raise HTTPException(status_code=429, detail="Too many login attempts from this network. Try again later.")


def _record_login_failure_for_rate_limit(ip: str | None) -> None:
    if not ip:
        return
    _failed_login_attempts.setdefault(ip, []).append(time.monotonic())


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str, config: dict):
    auth_cfg = config["auth"]
    secure = auth_cfg.get("cookie_secure", False)
    response.set_cookie(
        "access",
        access_token,
        httponly=True,
        secure=secure,
        samesite="strict",
        max_age=auth_cfg["access_ttl_minutes"] * 60,
    )
    response.set_cookie(
        "refresh",
        refresh_token,
        httponly=True,
        secure=secure,
        samesite="strict",
        max_age=auth_cfg["refresh_ttl_days"] * 86400,
    )
    # Double-submit CSRF token — deliberately NOT httponly, base.js's apiFetch
    # reads it and echoes it back as X-CSRF-Token on mutating requests (see
    # deps.verify_csrf). Same TTL as the access cookie it accompanies.
    response.set_cookie(
        "csrf",
        secrets.token_urlsafe(32),
        httponly=False,
        secure=secure,
        samesite="strict",
        max_age=auth_cfg["access_ttl_minutes"] * 60,
    )


def _issue_tokens(db: Database, user: dict, config: dict, request: Request) -> tuple[str, str]:
    auth_cfg = config["auth"]
    access_token = create_access_token(
        user["id"],
        user["token_version"],
        auth_cfg["jwt_secret"],
        auth_cfg["access_ttl_minutes"],
    )
    refresh_token = generate_refresh_token()
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(days=auth_cfg["refresh_ttl_days"])
    db.create_session(
        user_id=user["id"],
        refresh_token_hash=hash_refresh_token(refresh_token),
        expires_at=expires_at,
        user_agent=request.headers.get("User-Agent"),
        ip=client_ip(request),
    )
    return access_token, refresh_token


def _user_out(db: Database, user: dict) -> UserOut:
    role_name = db.get_role_name(user["role_id"])
    permissions = db.resolve_effective_permissions(user["id"])
    return UserOut(
        id=user["id"],
        email=user["email"],
        full_name=user.get("full_name"),
        is_admin=user["is_admin"],
        role=role_name,
        permissions=sorted(permissions),
    )


@router.post("/auth/login", response_model=TokenResponse)
async def login(
    req: LoginRequest,
    response: Response,
    request: Request,
    db: Database = Depends(get_db),
    config: dict = Depends(get_config),
):
    ip = client_ip(request)
    _check_login_rate_limit(ip, config)

    user = db.get_user_by_email(req.email)
    if not user:
        # Pay the same argon2 cost a wrong-password attempt would, so response
        # timing can't be used to enumerate which emails have accounts.
        verify_password(DUMMY_PASSWORD_HASH, req.password)
        _record_login_failure_for_rate_limit(ip)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if user["locked_until"] and user["locked_until"] > datetime.datetime.utcnow():
        raise HTTPException(status_code=423, detail="Account temporarily locked. Try again later.")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="Account is disabled")

    with db._Session() as s:
        row = s.query(User.password_hash).filter_by(id=user["id"]).first()
        password_hash = row[0] if row else ""

    if not verify_password(password_hash, req.password):
        db.record_login_failure(user["id"], config["auth"]["lockout_threshold"], config["auth"]["lockout_minutes"])
        db.write_audit(user["id"], "user.login_failed", "user", user["id"], ip=ip)
        _record_login_failure_for_rate_limit(ip)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    db.record_login_success(user["id"])
    access_token, refresh_token = _issue_tokens(db, user, config, request)
    _set_auth_cookies(response, access_token, refresh_token, config)
    db.write_audit(user["id"], "user.login", "user", user["id"], ip=ip)

    return TokenResponse(access_token=access_token, refresh_token=refresh_token, user=_user_out(db, user))


@router.post("/auth/refresh", response_model=TokenResponse)
async def refresh(
    req: RefreshRequest,
    request: Request,
    response: Response,
    db: Database = Depends(get_db),
    config: dict = Depends(get_config),
):
    presented = req.refresh_token or request.cookies.get("refresh")
    if not presented:
        raise HTTPException(status_code=401, detail="No refresh token provided")

    token_hash = hash_refresh_token(presented)
    session = db.get_session_by_hash(token_hash)
    if session and session["revoked_at"]:
        # A rotated-away token was presented again: possible theft/replay — kill the family.
        db.revoke_session_family(session["user_id"])
        db.write_audit(session["user_id"], "user.session_reuse_detected", "session", session["id"])
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    if not session or session["expires_at"] < datetime.datetime.utcnow():
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user = db.get_user_by_id(session["user_id"])
    if not user or not user["is_active"]:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    auth_cfg = config["auth"]
    new_refresh_token = generate_refresh_token()
    new_expires_at = datetime.datetime.utcnow() + datetime.timedelta(days=auth_cfg["refresh_ttl_days"])
    db.rotate_session(session["id"], hash_refresh_token(new_refresh_token), new_expires_at)

    access_token = create_access_token(
        user["id"],
        user["token_version"],
        auth_cfg["jwt_secret"],
        auth_cfg["access_ttl_minutes"],
    )
    _set_auth_cookies(response, access_token, new_refresh_token, config)

    return TokenResponse(access_token=access_token, refresh_token=new_refresh_token, user=_user_out(db, user))


@router.post("/auth/logout")
async def logout(
    request: Request, response: Response, req: RefreshRequest | None = None, db: Database = Depends(get_db)
):
    """Accepts the refresh token explicitly (agent/identity.py's logout —
    it's a Bearer client, not a cookie jar, same as /auth/refresh's existing
    dual-mode) or falls back to the `refresh` cookie (browser)."""
    presented = (req.refresh_token if req else None) or request.cookies.get("refresh")
    if presented:
        session = db.get_session_by_hash(hash_refresh_token(presented))
        if session:
            db.revoke_session(session["id"])
            db.write_audit(session["user_id"], "user.logout", "session", session["id"])
    response.delete_cookie("access")
    response.delete_cookie("refresh")
    response.delete_cookie("csrf")
    return {"message": "Logged out"}


@router.get("/auth/me", response_model=UserOut)
async def me(user: CurrentUser = Depends(get_current_user), db: Database = Depends(get_db)):
    db_user = db.get_user_by_id(user.id)
    return _user_out(db, db_user)


def _current_session_id(request: Request, db: Database) -> int | None:
    presented = request.cookies.get("refresh")
    if not presented:
        return None
    session = db.get_session_by_hash(hash_refresh_token(presented))
    return session["id"] if session else None


@router.get("/auth/sessions")
async def list_sessions(
    request: Request, user: CurrentUser = Depends(get_current_user), db: Database = Depends(get_db)
):
    """Self-service session list for the logged-in user — powers the "My
    Sessions" panel in both frontends' headers."""
    current_id = _current_session_id(request, db)
    sessions = db.list_active_sessions(user.id)
    return [
        {
            "id": s["id"],
            "user_agent": s["user_agent"],
            "ip": s["ip"],
            "created_at": s["created_at"],
            "last_used_at": s["last_used_at"],
            "expires_at": s["expires_at"],
            "is_current": s["id"] == current_id,
        }
        for s in sessions
    ]


@router.delete("/auth/sessions/{session_id}", dependencies=[Depends(verify_csrf)])
async def revoke_session(
    session_id: int, user: CurrentUser = Depends(get_current_user), db: Database = Depends(get_db)
):
    session = db.get_session_by_id(session_id)
    if not session or session["user_id"] != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    db.revoke_session(session_id)
    db.write_audit(user.id, "user.session_revoke", "session", session_id)
    return {"message": "Session revoked"}


@router.post("/auth/sessions/revoke-others", dependencies=[Depends(verify_csrf)])
async def revoke_other_sessions(
    request: Request, user: CurrentUser = Depends(get_current_user), db: Database = Depends(get_db)
):
    current_id = _current_session_id(request, db)
    count = db.revoke_sessions_except(user.id, keep_session_id=current_id)
    db.write_audit(user.id, "user.session_revoke_others", "user", user.id, detail={"count": count})
    return {"message": f"Revoked {count} other session(s)"}
