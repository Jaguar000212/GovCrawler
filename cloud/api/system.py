"""System activity aggregation for the browser admin dashboard, plus
/healthz. The desktop-launcher-facing counterparts (`/api/system/activity`,
`/api/system/cancel-all`) moved to the agent's own standalone BFF
(agent/bff/*) — the cloud process never runs a crawl engine itself anymore
(plan.md §19.1 Phase 9 Part 2), so it has nothing local to report on or
cancel; crawl-job activity here is DB-backed (`crawl_jobs.status`), not an
in-process task registry. See .docs/api-reference.md."""

import logging
from fastapi import APIRouter, Depends, Response
from sqlalchemy import text

from . import campaigns as campaigns_module
from .deps import get_config, get_db, require
from ..db import Campaign, CampaignStatus, Database

log = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


@router.get("/healthz")
async def healthz(response: Response, db: Database = Depends(get_db)):
    """Public liveness/readiness probe for the proxy/orchestrator — no auth,
    no loopback restriction (deploy/docker-compose.yml's api healthcheck and
    any external uptime monitor both need to reach this)."""
    try:
        with db._Session() as s:
            s.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception:
        log.error("Health check failed — DB unreachable", exc_info=True)
        response.status_code = 503
        return {"status": "db_unreachable"}


def _running_campaigns_without_task(db: Database, known_ids: set[int]) -> list[dict]:
    # Test-campaign dispatch (and any campaign whose task handle was lost to a
    # process restart) has no task in _active_campaign_tasks, so DB status is
    # the only signal for those. Can go stale if the process was killed
    # mid-dispatch in a previous run — a pre-existing gap, not something this
    # endpoint can fully close.
    with db._Session() as s:
        q = s.query(Campaign).filter(Campaign.status == CampaignStatus.RUNNING)
        if known_ids:
            q = q.filter(Campaign.id.notin_(known_ids))
        return [{"id": c.id, "name": c.name} for c in q.all()]


def _get_admin_activity(db: Database) -> dict:
    """Org-wide live counts for the browser admin dashboard: crawl jobs come
    straight from `crawl_jobs.status == 'running'` (DB-backed — there is no
    in-process task registry to read on the cloud side anymore); campaign
    dispatch genuinely still runs in this process (the dispatcher), so that
    half stays task-registry-backed."""
    crawl_jobs = [
        {
            "id": j["id"],
            "label": f"Job #{j['id']} ({j['crawled_domains']}/{j['total_domains']} domains, {j['leads_found']} leads)",
            "agent_hostname": j["agent_hostname"],
        }
        for j in db.get_running_jobs()
    ]

    campaigns = []
    tracked_ids = set()
    for campaign_id, task in campaigns_module._active_campaign_tasks.items():
        if task.done():
            continue
        tracked_ids.add(campaign_id)
        campaign = db.get_campaign(campaign_id, view_all=True)
        campaigns.append({"id": campaign_id, "name": campaign["name"] if campaign else f"Campaign #{campaign_id}"})

    campaigns.extend(_running_campaigns_without_task(db, tracked_ids))
    for campaign in campaigns:
        campaign["stats"] = db.get_campaign_stats(campaign["id"])

    recent_jobs = db.list_jobs(limit=5, view_all=True)
    recent_campaigns, _ = db.list_campaigns(limit=5, view_all=True)

    return {
        "crawl_jobs": crawl_jobs,
        "campaigns": campaigns,
        "total_active": len(crawl_jobs) + len(campaigns),
        "recent_jobs": [j for j in recent_jobs if j["status"] not in ("pending", "running")],
        "recent_campaigns": [c for c in recent_campaigns if c["status"] not in ("RUNNING",)],
    }


@router.get("/api/admin/activity", dependencies=[Depends(require("jobs.view_all"))])
async def get_admin_activity(db: Database = Depends(get_db)):
    return _get_admin_activity(db)


@router.get("/api/admin/system-status", dependencies=[Depends(require("jobs.view_all"))])
async def get_system_status(db: Database = Depends(get_db), config: dict = Depends(get_config)):
    """Backs the admin dashboard's System tab — the same DB check /healthz
    does, plus config-derived dispatch mode and a summary of which agents
    have ever run a job here. No new tables; everything is derived from
    existing data (plan.md §19.1 UI overhaul)."""
    try:
        with db._Session() as s:
            s.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        log.error("System-status DB check failed", exc_info=True)
        db_status = "db_unreachable"

    return {
        "db_status": db_status,
        "dispatch_mode": config.get("dispatch", {}).get("mode", "embedded"),
        "agents": db.get_agent_summary(),
    }
