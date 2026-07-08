"""Agent-rendered operator pages — verbatim reuse of the shared frontend/
templates (no cloud.* import needed: frontend/ is a top-level sibling asset
directory, not under cloud/). Data loads via the relative fetch() calls the
existing page JS already makes, proxied to the cloud by proxy.py. The admin
dashboard is deliberately NOT mounted here (plan.md §19.1 Phase 9 Part 2,
judgment call #3) — an admin-capable operator gets an external link instead
(base.html's Admin nav button, gated on jobs.view_all, opens `cloud_admin_url`
in a new tab)."""

import logging
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import security
from .local_auth import _cloud_base_url
from portal.paths import APP_DIR

log = logging.getLogger(__name__)

router = APIRouter(tags=["pages"], dependencies=[Depends(security.require_loopback)])

_frontend_dir = APP_DIR / "frontend"
_template_dirs = [str(_frontend_dir / "agent" / "templates"), str(_frontend_dir / "shared" / "templates")]
_templates = Jinja2Templates(directory=_template_dirs)


def _cloud_admin_url() -> str:
    try:
        return _cloud_base_url()
    except Exception:
        return ""


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    template = _templates.get_template("login.html")
    return HTMLResponse(template.render({"request": request, "active_page": "login"}))


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, user=Depends(security.current_operator_or_redirect)):
    template = _templates.get_template("index.html")
    return HTMLResponse(
        template.render(
            {"request": request, "active_page": "dashboard", "user": user, "cloud_admin_url": _cloud_admin_url()}
        )
    )


@router.get("/leads", response_class=HTMLResponse)
async def leads_page(request: Request, user=Depends(security.current_operator_or_redirect)):
    template = _templates.get_template("leads.html")
    return HTMLResponse(
        template.render(
            {"request": request, "active_page": "leads", "user": user, "cloud_admin_url": _cloud_admin_url()}
        )
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user=Depends(security.current_operator_or_redirect)):
    template = _templates.get_template("settings.html")
    return HTMLResponse(
        template.render(
            {"request": request, "active_page": "settings", "user": user, "cloud_admin_url": _cloud_admin_url()}
        )
    )


@router.get("/test-campaign", response_class=HTMLResponse)
async def test_campaign_page(request: Request, user=Depends(security.current_operator_or_redirect)):
    template = _templates.get_template("test-campaign.html")
    return HTMLResponse(
        template.render(
            {"request": request, "active_page": "test-campaign", "user": user, "cloud_admin_url": _cloud_admin_url()}
        )
    )


@router.get("/campaigns", response_class=HTMLResponse)
async def campaigns_page(request: Request, user=Depends(security.current_operator_or_redirect)):
    template = _templates.get_template("campaigns.html")
    return HTMLResponse(
        template.render(
            {"request": request, "active_page": "campaigns", "user": user, "cloud_admin_url": _cloud_admin_url()}
        )
    )


@router.get("/user-guide", response_class=HTMLResponse)
async def user_guide_page(request: Request, user=Depends(security.current_operator_or_redirect)):
    template = _templates.get_template("user-guide.html")
    return HTMLResponse(
        template.render(
            {"request": request, "active_page": "user-guide", "user": user, "cloud_admin_url": _cloud_admin_url()}
        )
    )
