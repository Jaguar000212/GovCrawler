"""
FastAPI app factory for the GovCrawler Portal.

Route definitions live in per-concern modules (frontend, domains, config,
imports, jobs, leads, templates, blacklist, campaigns, credentials); this
module only builds the FastAPI app, mounts static files, manages the
Playwright browser lifespan, and wires shared state into cloud.api.deps.

Also mounts `agent.api.router` — today both tiers still run in one process
(see agent/api.py's docstring), so this is the one place a cloud-tier module
imports from `agent/` at all.

See each route module's docstring for its endpoint list.
"""

import asyncio
import logging
import os
import secrets
import yaml
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from playwright.async_api import async_playwright

from . import (
    admin, auth, blacklist, campaigns, config, coordination, credentials, deps, domains, frontend, imports, jobs,
    leads, system, templates,
)
from .deps import RedirectException, get_current_user
from ..db import Database
from agent import api as agent_api
from portal.paths import LIVE_CONFIG_PATH

log = logging.getLogger(__name__)


def _ensure_jwt_secret(config_dict: dict, config_path: Path) -> None:
    """Generate + persist a random JWT secret on first run so it survives restarts.

    Containers (deploy/docker-compose.yml) supply JWT_SECRET via env instead —
    skip the file write there, since config.yaml isn't guaranteed writable/
    persistent inside the image."""
    if os.environ.get("JWT_SECRET"):
        config_dict.setdefault("auth", {})["jwt_secret"] = os.environ["JWT_SECRET"]
        return
    if config_dict.get("auth", {}).get("jwt_secret"):
        return
    config_dict.setdefault("auth", {})["jwt_secret"] = secrets.token_urlsafe(48)
    with open(config_path, "w") as f:
        yaml.dump(config_dict, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    log.info("Generated and persisted a new auth.jwt_secret")


_REAP_INTERVAL_SECONDS = 60
_REAP_THRESHOLD_SECONDS = 150  # lenient vs. per_url_timeout (~100s) + jitter, per plan.md §10.6


async def _reap_loop():
    while True:
        await asyncio.sleep(_REAP_INTERVAL_SECONDS)
        try:
            reaped = deps._db.reap_stale_jobs(_REAP_THRESHOLD_SECONDS)
            if reaped:
                log.warning(f"Reaped {len(reaped)} stale job(s) (no heartbeat for "
                           f"{_REAP_THRESHOLD_SECONDS}s+): {reaped}")
        except Exception:
            log.error("Stale-job reaper sweep failed", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting Playwright browser…")
    deps._playwright_instance = await async_playwright().start()
    deps._browser = await deps._playwright_instance.chromium.launch(headless=True)
    log.info("Browser ready.")
    reap_task = asyncio.create_task(_reap_loop())
    yield
    reap_task.cancel()
    try:
        await reap_task
    except asyncio.CancelledError:
        pass
    log.info("Shutting down browser…")
    try:
        await deps._browser.close()
        await deps._playwright_instance.stop()
    except Exception:
        pass


def create_app(config_dict: dict, db: Database) -> FastAPI:
    deps._db = db
    deps._config = config_dict
    deps._config_path = LIVE_CONFIG_PATH

    _ensure_jwt_secret(config_dict, deps._config_path)

    app = FastAPI(title="GovCrawler Portal", lifespan=lifespan)

    @app.exception_handler(RedirectException)
    async def _redirect_handler(request: Request, exc: RedirectException):
        return RedirectResponse(url=exc.location, status_code=302)

    # Mount static files
    static_dir = Path(__file__).parent.parent / "frontend" / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(frontend.router)
    app.include_router(domains.router, dependencies=[Depends(get_current_user)])
    app.include_router(config.router, dependencies=[Depends(get_current_user)])
    app.include_router(imports.router, dependencies=[Depends(get_current_user)])
    app.include_router(jobs.router, dependencies=[Depends(get_current_user)])
    app.include_router(agent_api.router, dependencies=[Depends(get_current_user)])
    app.include_router(coordination.router, dependencies=[Depends(get_current_user)])
    app.include_router(leads.router, dependencies=[Depends(get_current_user)])
    app.include_router(templates.router, dependencies=[Depends(get_current_user)])
    app.include_router(blacklist.router, dependencies=[Depends(get_current_user)])
    app.include_router(campaigns.router, dependencies=[Depends(get_current_user)])
    app.include_router(credentials.router, dependencies=[Depends(get_current_user)])
    app.include_router(system.router)

    return app
