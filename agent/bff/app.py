"""The agent's standalone local BFF — replaces the old combined cloud+agent
process the launcher used to boot (plan.md §19.1 Phase 9 Part 2, 2.3). Owns
Playwright/the crawl browser directly (moved out of cloud/api/server.py,
which never touches Playwright again — the cloud tier is genuinely
crawler-free from here on) and mounts agent/api.py's job-lifecycle routes
behind the loopback + local-session + CSRF guards in security.py. See
.docs/architecture.md.
"""

import logging
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright

from . import local_auth, local_system, pages, proxy, security
from .proxy import CloudUnreachableError
from .. import api as agent_api
from .. import state
from portal.paths import APP_DIR
from shared.errors import format_validation_errors

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting Playwright browser…")
    playwright_instance = await async_playwright().start()
    browser = await playwright_instance.chromium.launch(headless=True)
    state.set_browser(browser)
    log.info("Browser ready.")
    yield
    log.info("Shutting down browser…")
    try:
        await browser.close()
        await playwright_instance.stop()
    except Exception:
        pass


def create_app(config: dict) -> FastAPI:
    state.set_config(config)
    app = FastAPI(title="GovCrawler Agent", lifespan=lifespan)

    @app.exception_handler(security.LocalRedirectException)
    async def _redirect_handler(request: Request, exc: security.LocalRedirectException):
        return RedirectResponse(url=exc.location, status_code=302)

    @app.exception_handler(CloudUnreachableError)
    async def _cloud_unreachable_handler(request: Request, exc: CloudUnreachableError):
        return JSONResponse(status_code=502, content={"detail": exc.message, "code": "cloud_unreachable"})

    # Every error response is guaranteed a plain-string `detail` — see
    # cloud/api/server.py's identical handlers and shared/errors.py.
    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError):
        content = {"detail": format_validation_errors(exc), "code": "validation_error"}
        return JSONResponse(status_code=422, content=content)

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception):
        log.error("Unhandled exception on %s %s", request.method, request.url.path, exc_info=True)
        content = {"detail": "Something went wrong on the agent.", "code": "internal_error"}
        return JSONResponse(status_code=500, content=content)

    @app.get("/ping", dependencies=[Depends(security.require_loopback)])
    async def ping():
        """Readiness probe for the launcher's own startup poll
        (`_wait_for_server_ready`) — needs no session, unlike almost
        everything else this app serves."""
        return {"status": "ok"}

    static_dir = APP_DIR / "frontend" / "agent" / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    shared_static_dir = APP_DIR / "frontend" / "shared" / "static"
    app.mount("/assets", StaticFiles(directory=str(shared_static_dir)), name="assets")

    # Registration order matters: FastAPI matches path+method in the order
    # routes were added, so the specific routers below must all precede
    # proxy.router's catch-all "/api/{path:path}".
    app.include_router(local_auth.router)
    app.include_router(local_system.router)
    app.include_router(pages.router)
    app.include_router(
        agent_api.router,
        dependencies=[
            Depends(security.require_loopback),
            Depends(security.require_local_session),
            Depends(security.verify_local_csrf),
        ],
    )
    app.include_router(proxy.router)

    return app
