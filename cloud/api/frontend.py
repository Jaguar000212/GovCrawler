"""Frontend HTML page routes plus small UI-support endpoints. The cloud tier
only ever renders the admin UI (frontend/cloud/templates) plus the shared
login page (frontend/shared/templates) — the crawler/outreach pages
(dashboard, leads, campaigns, settings, test-campaign) are agent-only, see
agent/bff/pages.py. See .docs/api-reference.md."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .deps import CurrentUser, current_user_or_redirect, get_current_user, require
from portal.paths import APP_DIR, LOG_FILE_PATH

router = APIRouter(tags=["frontend"])

_frontend_dir = APP_DIR / "frontend"
_template_dirs = [str(_frontend_dir / "cloud" / "templates"), str(_frontend_dir / "shared" / "templates")]
_templates = Jinja2Templates(directory=_template_dirs)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    template = _templates.get_template("login.html")
    return HTMLResponse(template.render({"request": request, "active_page": "login"}))


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, user: CurrentUser = Depends(require("jobs.view_all"))):
    template = _templates.get_template("admin-dashboard.html")
    return HTMLResponse(template.render({"request": request, "active_page": "admin-dashboard", "user": user}))


@router.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard_page(request: Request, user: CurrentUser = Depends(require("jobs.view_all"))):
    template = _templates.get_template("admin-dashboard.html")
    return HTMLResponse(template.render({"request": request, "active_page": "admin-dashboard", "user": user}))


@router.get("/user-guide", response_class=HTMLResponse)
async def admin_guide_page(request: Request, user: CurrentUser = Depends(current_user_or_redirect)):
    template = _templates.get_template("admin-guide.html")
    return HTMLResponse(template.render({"request": request, "active_page": "admin-guide", "user": user}))


@router.get("/api/logs")
async def get_logs(user: CurrentUser = Depends(get_current_user)):
    log_file = LOG_FILE_PATH
    if not log_file.exists():
        return {"logs": "Log file not found."}
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-1000:]
        return {"logs": "".join(lines)}
    except Exception as e:
        return {"logs": f"Failed to read logs: {e}"}
