"""User administration endpoints (create/list users, set active/role, reset
password, permission overrides, list roles) plus the audit log reader. See
.docs/authentication.md and .docs/api-reference.md."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from shared.permissions import PERMISSIONS
from .deps import CurrentUser, client_ip, get_db, require
from ..db import Database

router = APIRouter(tags=["admin"], dependencies=[Depends(require("users.manage"))])

_MIN_PASSWORD_LENGTH = 8


def _validate_password_strength(v: str) -> str:
    if len(v) < _MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {_MIN_PASSWORD_LENGTH} characters")
    return v


class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str | None = None
    role: str | None = None
    is_admin: bool = False

    _validate_password = field_validator("password")(_validate_password_strength)


class UserPatch(BaseModel):
    is_active: bool | None = None
    role: str | None = None


class PasswordReset(BaseModel):
    password: str

    _validate_password = field_validator("password")(_validate_password_strength)


class PermissionOverrideSet(BaseModel):
    effect: str | None = None  # "grant" | "deny" | None (None clears the override)


@router.get("/api/admin/users")
async def list_users(db: Database = Depends(get_db)):
    users = db.list_users()
    for u in users:
        u["role"] = db.get_role_name(u["role_id"])
    return users


@router.get("/api/admin/users/{user_id}")
async def get_user_detail(user_id: int, db: Database = Depends(get_db)):
    """Adds resolved effective permissions + raw per-permission overrides on
    top of the list view, so the admin UI can render a grant/deny/inherited
    grid per PERMISSIONS key."""
    u = db.get_user_by_id(user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    u["role"] = db.get_role_name(u["role_id"])
    u["effective_permissions"] = sorted(db.resolve_effective_permissions(user_id))
    u["permission_overrides"] = db.list_user_permission_overrides(user_id)
    return u


@router.post("/api/admin/users", status_code=201)
async def create_user(
    req: UserCreate,
    request: Request,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require("users.manage")),
):
    if db.get_user_by_email(req.email):
        raise HTTPException(status_code=409, detail="A user with this email already exists")
    user_id = db.create_user(
        email=req.email,
        password=req.password,
        full_name=req.full_name,
        is_admin=req.is_admin,
        role_name=req.role,
        created_by=user.id,
    )
    db.write_audit(user.id, "user.create", "user", user_id, ip=client_ip(request))
    return {"id": user_id, "message": "User created"}


@router.patch("/api/admin/users/{user_id}")
async def patch_user(
    user_id: int,
    req: UserPatch,
    request: Request,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require("users.manage")),
):
    if req.is_active is False:
        target = db.get_user_by_id(user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        if target["is_admin"] and target["is_active"] and db.count_active_admins() <= 1:
            raise HTTPException(status_code=409, detail="Cannot deactivate the last remaining admin account")
    if req.is_active is not None:
        if not db.set_user_active(user_id, req.is_active):
            raise HTTPException(status_code=404, detail="User not found")
        db.write_audit(
            user.id, "user.set_active", "user", user_id, detail={"is_active": req.is_active}, ip=client_ip(request)
        )
    if req.role is not None:
        if not db.set_user_role(user_id, req.role):
            raise HTTPException(status_code=404, detail="User or role not found")
        db.write_audit(user.id, "user.set_role", "user", user_id, detail={"role": req.role}, ip=client_ip(request))
    return {"message": "User updated"}


@router.post("/api/admin/users/{user_id}/reset-password")
async def reset_password(
    user_id: int,
    req: PasswordReset,
    request: Request,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require("users.manage")),
):
    if not db.set_password(user_id, req.password):
        raise HTTPException(status_code=404, detail="User not found")
    db.write_audit(user.id, "user.reset_password", "user", user_id, ip=client_ip(request))
    return {"message": "Password reset"}


@router.put("/api/admin/users/{user_id}/permissions/{permission_key}")
async def set_user_permission(
    user_id: int,
    permission_key: str,
    req: PermissionOverrideSet,
    request: Request,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require("users.manage")),
):
    """Grant/deny/clear a single permission override on top of the target
    user's role bundle (`shared.permissions.PERMISSIONS` is the whole
    catalog). Role bundles themselves can be edited via cloud/api/roles.py
    (gated by `roles.manage`) — this endpoint is for per-user exceptions on
    top of whatever role a user is assigned."""
    if permission_key not in PERMISSIONS:
        raise HTTPException(status_code=404, detail=f"Unknown permission: {permission_key}")
    if req.effect is not None and req.effect not in ("grant", "deny"):
        raise HTTPException(status_code=400, detail="effect must be 'grant', 'deny', or null")
    if not db.set_user_permission_override(user_id, permission_key, req.effect):
        raise HTTPException(status_code=404, detail="User not found")
    db.write_audit(
        user.id,
        "user.permission_override_set",
        "user",
        user_id,
        detail={"permission_key": permission_key, "effect": req.effect},
        ip=client_ip(request),
    )
    return {"message": "Permission override updated"}


@router.get("/api/admin/roles")
async def list_roles(db: Database = Depends(get_db)):
    return db.list_roles()


@router.get("/api/admin/permissions")
async def list_permissions():
    """The full catalog (key -> description), so the UI can render the
    override grid without hardcoding `shared.permissions.PERMISSIONS`."""
    return PERMISSIONS
