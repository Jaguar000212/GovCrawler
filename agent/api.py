"""Agent-tier BFF routes that own a running crawl (create / resume / cancel).

These talk to the cloud over the coordination HTTP contract via cloud_client.py,
authenticated as the operator's own standing session (agent/identity.py) rather
than whatever token happened to authenticate one browser request — a crawl can
run far longer than one access token's TTL, and the identity module already
knows how to refresh itself via /auth/refresh + the OS keyring. Zero cloud.*
imports (plan.md §19.1 Phase 9): config/browser/active_tasks are agent-owned
state (agent/state.py, wired by agent/bff/app.py's own lifespan — the standalone
local BFF process, Part 2's 2.3).

These routes no longer verify the caller's own permissions (that moved to
cloud/api/coordination.py's require("crawl.run"), the one place that can still
check it) — a bare loopback restriction (agent/bff/security.py's
require_loopback) is the remaining gate here, matching the same posture as
/api/system/activity.
"""

import asyncio
import httpx
import logging
import time
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, model_validator

from . import identity, localdb, state
from .bff.security import require_loopback
from .cloud_client import CloudApiClient, create_remote_job, resume_remote_job
from .crawler.engine import CrawlerEngine
from portal.paths import DATA_DIR
from shared.urls import strip_www

log = logging.getLogger(__name__)

router = APIRouter(tags=["agent"])


class StartJobRequest(BaseModel):
    domain_ids: list[int] | None = None
    custom_urls: list[str] | None = None
    category_filter: str | None = None
    title_filter: str | None = None

    @model_validator(mode="after")
    def _check_exclusive_source(self):
        if bool(self.domain_ids) == bool(self.custom_urls):
            raise ValueError("Provide exactly one of domain_ids or custom_urls")
        return self


def _cloud_base_url(config: dict) -> str:
    """agent/localdb.py's `cloud_api_base_url` local setting is the real
    remote-VPS address once one is configured (plan.md §19.1 Phase 9 Part 2,
    2.1) — set via the launcher's first-run "Cloud Server URL" prompt.
    Falls back to this same process's own loopback address for the
    transitional period where agent/api.py is still mounted inside the
    combined cloud+agent app (Part 2's later steps retire that combined
    mode entirely, at which point this fallback stops being reachable)."""
    configured = localdb.get_setting("cloud_api_base_url")
    if configured:
        return configured
    if config.get("cloud_api_base_url"):
        return config["cloud_api_base_url"]
    port = config.get("api", {}).get("port", 8001)
    return f"http://127.0.0.1:{port}"


def _local_visited_bootstrap(seeds: list[tuple[str, int | None]], recrawl_days: int) -> list[str]:
    """Recently-visited URLs from THIS machine's own crawl history
    (agent/localdb.py), excluding this job's own seed domains (those must
    stay freely re-crawlable regardless of recency) — the local replacement
    for the old cloud-side cross-agent recrawl protection (plan.md §19.1
    Phase 9 Part 2, 2.2 — visited-URL data is no longer shared across agents
    at all, only leads are)."""
    seed_roots = set()
    for url, _ in seeds:
        parsed = url if "://" in url else "http://" + url
        seed_roots.add(strip_www(urlsplit(parsed).netloc.lower()))
    since_ts = time.time() - recrawl_days * 86400
    bootstrap = []
    for url in localdb.get_recently_visited(since_ts):
        root = strip_www(urlsplit(url).netloc.lower())
        if not any(root == r or root.endswith("." + r) for r in seed_roots):
            bootstrap.append(url)
    return bootstrap


async def _run_crawl(job_id: int, seeds: list[tuple[str, int | None]], visited_bootstrap: list[str],
                     cloud: CloudApiClient, config: dict, browser,
                     active_tasks: dict[int, asyncio.Task], frontier: dict | None = None):
    log.info(f"Crawl job {job_id} starting with {len(seeds)} seeds"
            + (" (resumed from checkpoint)" if frontier else ""))
    cloud.start()
    try:
        engine = CrawlerEngine(config=config, cloud=cloud, job_id=job_id, browser=browser)
        await engine.run(seeds, visited_bootstrap=visited_bootstrap, frontier=frontier)
        await cloud.finish_job(status="done")
        cloud.clear_frontier()
        log.info(f"Crawl job {job_id} done.")
    except asyncio.CancelledError:
        log.info(f"Crawl job {job_id} cancelled.")
        await cloud.best_effort_drain()
        await cloud.finish_job(status="cancelled")
        raise
    except Exception as e:
        log.error(f"Crawl job {job_id} failed: {e}", exc_info=True)
        await cloud.finish_job(status="failed", error=str(e))
    finally:
        active_tasks.pop(job_id, None)
        await cloud.aclose()


@router.post("/api/jobs")
async def create_job(req: StartJobRequest, request: Request):
    require_loopback(request)
    config = state.get_config()
    browser = state.get_browser()
    active_tasks = state.get_active_tasks()
    base_url = _cloud_base_url(config)

    body = {"domain_ids": req.domain_ids, "custom_urls": req.custom_urls,
           "category_filter": req.category_filter, "title_filter": req.title_filter,
           "agent_id": localdb.get_agent_id()}
    try:
        created = await create_remote_job(base_url, identity.get_valid_token, identity.refresh, **body)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)

    job_id = created["job_id"]
    seeds = [(s[0], s[1]) for s in created["seeds"]]
    engine_config = created["policy"]
    recrawl_days = engine_config.get("crawler", {}).get("recrawl_days", 30)
    visited_bootstrap = _local_visited_bootstrap(seeds, recrawl_days)

    outbox_path = DATA_DIR / f"outbox_job_{job_id}.db"
    cloud = CloudApiClient(base_url, identity.get_valid_token, job_id, outbox_path, refresh=identity.refresh)
    task = asyncio.create_task(_run_crawl(job_id, seeds, visited_bootstrap,
                                          cloud, engine_config, browser, active_tasks))
    active_tasks[job_id] = task

    return {"id": job_id,
            "message": f"Crawl started for {len(seeds)} seed URL(s)"}


@router.post("/api/jobs/{job_id}/resume")
async def resume_job(job_id: int, request: Request):
    """Manual resume of an `interrupted` job — reloads the local frontier
    checkpoint (if this machine has one) and continues the queue instead of
    re-crawling from seeds. Automatic resume-on-process-restart (scanning for
    orphaned frontier files at startup) is a follow-up, not attempted here."""
    require_loopback(request)
    config = state.get_config()
    browser = state.get_browser()
    active_tasks = state.get_active_tasks()

    if job_id in active_tasks and not active_tasks[job_id].done():
        raise HTTPException(status_code=409, detail="Job is already running")

    base_url = _cloud_base_url(config)

    try:
        resumed = await resume_remote_job(base_url, identity.get_valid_token, identity.refresh, job_id,
                                          agent_id=localdb.get_agent_id())
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)

    outbox_path = DATA_DIR / f"outbox_job_{job_id}.db"
    cloud = CloudApiClient(base_url, identity.get_valid_token, job_id, outbox_path, refresh=identity.refresh)
    frontier = await cloud.load_frontier()
    if frontier is None:
        log.warning(f"Job {job_id}: no local frontier checkpoint found — resuming from seeds instead")

    seeds = [(s[0], s[1]) for s in resumed["seeds"]]
    recrawl_days = resumed["policy"].get("crawler", {}).get("recrawl_days", 30)
    visited_bootstrap = _local_visited_bootstrap(seeds, recrawl_days)
    task = asyncio.create_task(_run_crawl(job_id, seeds, visited_bootstrap,
                                          cloud, resumed["policy"], browser, active_tasks,
                                          frontier=frontier))
    active_tasks[job_id] = task

    return {"id": job_id,
            "message": "Crawl resumed" + (" from checkpoint" if frontier else " from seeds (no checkpoint found)")}


def cancel_job_if_running(job_id: int, active_tasks: dict[int, asyncio.Task]) -> bool:
    """Cancels this machine's local task if it's the one running the job.
    Returns whether it was running HERE — a real remote agent's job would
    return False (nothing local to cancel), relying entirely on the
    coordination cancel_requested flag being seen on its next heartbeat."""
    task = active_tasks.get(job_id)
    if task and not task.done():
        task.cancel()
        return True
    return False


@router.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: int, request: Request):
    require_loopback(request)
    config = state.get_config()
    active_tasks = state.get_active_tasks()
    base_url = _cloud_base_url(config)
    async with httpx.AsyncClient(base_url=base_url, timeout=10) as http:
        r = await http.post(f"/api/coordination/jobs/{job_id}/cancel",
                            headers={"Authorization": f"Bearer {await identity.get_valid_token()}"})
        if r.status_code == 401:
            await identity.refresh()
            r = await http.post(f"/api/coordination/jobs/{job_id}/cancel",
                                headers={"Authorization": f"Bearer {await identity.get_valid_token()}"})
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Job not found")
    if r.status_code == 403:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    r.raise_for_status()

    if cancel_job_if_running(job_id, active_tasks):
        return {"message": "Job cancelled"}
    return {"message": "Cancellation requested — the owning agent will stop it on its next heartbeat"}
