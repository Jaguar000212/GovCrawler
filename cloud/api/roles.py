"""Custom role CRUD — create/edit/clone/delete roles and their permission
bundles. See .docs/authentication.md and .docs/api-reference.md."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from shared.permissions import PERMISSIONS
from .deps import CurrentUser, client_ip, get_db, require
from ..db import Database

router = APIRouter(tags=["roles"], dependencies=[Depends(require("roles.manage"))])


class RoleCreate(BaseModel):
    name: str
    description: str | None = None
    permissions: list[str] = []


class RolePatch(BaseModel):
    description: str | None = None
    permissions: list[str] | None = None


class RoleClone(BaseModel):
    name: str


def _validate_permission_keys(keys: list[str]) -> None:
    unknown = sorted(set(keys) - PERMISSIONS.keys())
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown permission key(s): {', '.join(unknown)}")


@router.post("/api/admin/roles", status_code=201)
async def create_role(
    req: RoleCreate,
    request: Request,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require("roles.manage")),
):
    _validate_permission_keys(req.permissions)
    try:
        role_id = db.create_role(req.name, req.description, req.permissions)
    except IntegrityError:
        raise HTTPException(status_code=409, detail="A role with this name already exists")
    db.write_audit(
        user.id,
        "role.create",
        "role",
        role_id,
        detail={"name": req.name, "permissions": req.permissions},
        ip=client_ip(request),
    )
    return {"id": role_id, "message": "Role created"}


@router.patch("/api/admin/roles/{role_id}")
async def update_role(
    role_id: int,
    req: RolePatch,
    request: Request,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require("roles.manage")),
):
    if req.permissions is not None:
        _validate_permission_keys(req.permissions)
    try:
        updated = db.update_role(role_id, description=req.description, permission_keys=req.permissions)
    except ValueError:
        raise HTTPException(status_code=403, detail="Built-in roles cannot be modified")
    if not updated:
        raise HTTPException(status_code=404, detail="Role not found")
    db.write_audit(
        user.id,
        "role.update",
        "role",
        role_id,
        detail={"description": req.description, "permissions": req.permissions},
        ip=client_ip(request),
    )
    return {"message": "Role updated"}


@router.delete("/api/admin/roles/{role_id}")
async def delete_role(
    role_id: int,
    request: Request,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require("roles.manage")),
):
    try:
        deleted = db.delete_role(role_id)
    except ValueError as e:
        if str(e) == "system_role":
            raise HTTPException(status_code=403, detail="Built-in roles cannot be deleted")
        raise HTTPException(status_code=409, detail="Role is assigned to one or more users")
    if not deleted:
        raise HTTPException(status_code=404, detail="Role not found")
    db.write_audit(user.id, "role.delete", "role", role_id, ip=client_ip(request))
    return {"message": "Role deleted"}


@router.post("/api/admin/roles/{role_id}/clone", status_code=201)
async def clone_role(
    role_id: int,
    req: RoleClone,
    request: Request,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require("roles.manage")),
):
    source = db.get_role(role_id)
    if not source:
        raise HTTPException(status_code=404, detail="Role not found")
    try:
        new_role_id = db.create_role(req.name, source["description"], source["permissions"])
    except IntegrityError:
        raise HTTPException(status_code=409, detail="A role with this name already exists")
    db.write_audit(
        user.id,
        "role.clone",
        "role",
        new_role_id,
        detail={"cloned_from": role_id, "name": req.name},
        ip=client_ip(request),
    )
    return {"id": new_role_id, "message": "Role cloned"}
