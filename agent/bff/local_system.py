"""Agent-local system endpoints: this machine's own crawl-job activity and
its own log tail. The launcher-facing `/api/system/activity` and
`/api/system/cancel-all` used to live on the cloud process (when both tiers
shared one); now that a genuine agent-local task registry is the only thing
that ever existed here in practice, they live where the data actually is
(plan.md §19.1 Phase 9 Part 2, 2.4). The org-wide, admin-facing view is
`GET /api/admin/activity` on the cloud, unaffected — this only ever reports
what THIS machine is doing. `GET /api/logs` also moves here: the operator's
own crawl log is more useful locally than the VPS's server log (still
reachable only from the cloud-hosted admin dashboard)."""

import httpx
import logging
from fastapi import APIRouter, Depends

from portal.paths import LOG_FILE_PATH
from . import security
from .local_auth import _cloud_base_url
from .. import api as agent_api
from .. import identity, state
from ..cloud_client import request_with_retry

log = logging.getLogger(__name__)

router = APIRouter(
    tags=["local-system"],
    dependencies=[Depends(security.require_loopback), Depends(security.require_local_session)],
)


def _get_activity() -> dict:
    active_tasks = state.get_active_tasks()
    crawl_jobs = [{"id": job_id, "label": f"Job #{job_id}"} for job_id, task in active_tasks.items() if not task.done()]
    return {"crawl_jobs": crawl_jobs, "total_active": len(crawl_jobs)}


@router.get("/api/system/activity")
async def get_activity():
    return _get_activity()


@router.get("/api/logs")
async def get_logs():
    if not LOG_FILE_PATH.exists():
        return {"logs": "Log file not found."}
    try:
        with open(LOG_FILE_PATH, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-1000:]
        return {"logs": "".join(lines)}
    except Exception as e:
        return {"logs": f"Failed to read logs: {e}"}


@router.post("/api/system/cancel-all", dependencies=[Depends(security.verify_local_csrf)])
async def cancel_all():
    """Emergency stop for local shutdown: cancels this machine's own running
    jobs directly (no waiting for the engine to notice a heartbeat flag) and
    best-effort signals the cloud too, so a later resume attempt (from THIS
    same agent — plan.md §19.1 Phase 9 Part 2, 2.5) doesn't immediately
    resume something the operator just stopped."""
    active_tasks = state.get_active_tasks()
    job_ids = [job_id for job_id, task in active_tasks.items() if not task.done()]

    cloud_url = _cloud_base_url()
    async with httpx.AsyncClient(base_url=cloud_url, timeout=10) as http:
        for job_id in job_ids:
            try:
                await request_with_retry(
                    "POST",
                    http,
                    f"/api/coordination/jobs/{job_id}/cancel",
                    identity.get_valid_token,
                    identity.refresh,
                )
            except Exception as e:
                log.warning(f"cancel-all: coordination cancel signal failed for job {job_id}: {e}")

    cancelled = sum(1 for job_id in job_ids if agent_api.cancel_job_if_running(job_id, active_tasks))
    log.info(f"cancel-all: {cancelled} local crawl job(s) stopped")

    return {
        "crawl_jobs_cancelled": cancelled,
        "message": "Cancellation signalled for all locally-running jobs.",
    }
