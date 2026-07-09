"""User administration endpoints (create/list users, set active/role, reset
password, permission overrides, list roles) plus the audit log reader. See
.docs/authentication.md and .docs/api-reference.md."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from shared.permissions import PERMISSIONS
from .deps import CurrentUser, get_db, require
from ..db import Database

router = APIRouter(tags=["admin"], dependencies=[Depends(require("users.manage"))])


class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str | None = None
    role: str | None = None
    is_admin: bool = False


class UserPatch(BaseModel):
    is_active: bool | None = None
    role: str | None = None


class PasswordReset(BaseModel):
    password: str


class PermissionOverrideSet(BaseModel):
    effect: str | None = None  # "grant" | "deny" | None (None clears the override)


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


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
    db.write_audit(user.id, "user.create", "user", user_id, ip=_client_ip(request))
    return {"id": user_id, "message": "User created"}


@router.patch("/api/admin/users/{user_id}")
async def patch_user(
    user_id: int,
    req: UserPatch,
    request: Request,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require("users.manage")),
):
    if req.is_active is not None:
        if not db.set_user_active(user_id, req.is_active):
            raise HTTPException(status_code=404, detail="User not found")
        db.write_audit(
            user.id, "user.set_active", "user", user_id, detail={"is_active": req.is_active}, ip=_client_ip(request)
        )
    if req.role is not None:
        if not db.set_user_role(user_id, req.role):
            raise HTTPException(status_code=404, detail="User or role not found")
        db.write_audit(user.id, "user.set_role", "user", user_id, detail={"role": req.role}, ip=_client_ip(request))
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
    db.write_audit(user.id, "user.reset_password", "user", user_id, ip=_client_ip(request))
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
    catalog — role edits aren't supported, only per-user overrides, per
    plan.md §19.1 Phase 9 Part 2)."""
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
        ip=_client_ip(request),
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
