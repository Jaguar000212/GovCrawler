"""Audit-log reader (`audit.view` — deliberately separate from admin.py's
`users.manage`-gated router, since a user can hold one without the other).
See .docs/api-reference.md."""
import datetime
from fastapi import APIRouter, Depends, Query

from .deps import CurrentUser, get_db, require
from ..db import Database

router = APIRouter(tags=["audit"])


@router.get("/api/admin/audit")
async def get_audit_log(
        user_id: int = Query(None),
        action_prefix: str = Query(None),
        date_from: datetime.datetime = Query(None),
        date_to: datetime.datetime = Query(None),
        page: int = Query(1, ge=1),
        limit: int = Query(50, ge=1, le=200),
        db: Database = Depends(get_db),
        user: CurrentUser = Depends(require("audit.view")),
):
    entries, total = db.list_audit_log(
        user_id=user_id, action_prefix=action_prefix,
        date_from=date_from, date_to=date_to, page=page, limit=limit,
    )
    return {
        "entries": entries,
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
    }
