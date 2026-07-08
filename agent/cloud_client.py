"""CloudApiClient — mirrors the Database method surface the engine used to call,
but talks HTTP to cloud/api/coordination.py. `save_lead`/`mark_visited` are
fire-and-forget writes into the durable local outbox (agent/local_store.py);
`send_heartbeat`/`finish_job` go to the API directly. `token_provider` is an
async callable returning a currently-valid bearer token — on a 401 it's
refreshed and the call retried once, so a crawl outliving one access token's
TTL (the norm, not the exception, at hours-long crawl durations) never stalls
partway through. See .docs/resilience.md."""

import asyncio
import logging
import time

import httpx

from .local_store import LocalOutbox

log = logging.getLogger(__name__)

_BATCH_SIZE = 100
_FLUSH_IDLE_SLEEP = 2.0
_FLUSH_BUSY_SLEEP = 0.5
_BACKPRESSURE_THRESHOLD = 5000


async def _post_with_retry(http: httpx.AsyncClient, url: str, token_provider, refresh, **kwargs) -> httpx.Response:
    """POST with one retry-on-401 via `refresh()` — the shared shape every
    direct coordination call needs, since `token_provider` alone only ever
    returns the last-cached token."""
    r = await http.post(url, headers={"Authorization": f"Bearer {await token_provider()}"}, **kwargs)
    if r.status_code == 401:
        await refresh()
        r = await http.post(url, headers={"Authorization": f"Bearer {await token_provider()}"}, **kwargs)
    return r


async def _get_with_retry(http: httpx.AsyncClient, url: str, token_provider, refresh, **kwargs) -> httpx.Response:
    """GET counterpart to _post_with_retry — used by load_frontier's
    cross-machine fetch."""
    r = await http.get(url, headers={"Authorization": f"Bearer {await token_provider()}"}, **kwargs)
    if r.status_code == 401:
        await refresh()
        r = await http.get(url, headers={"Authorization": f"Bearer {await token_provider()}"}, **kwargs)
    return r


async def create_remote_job(base_url: str, token_provider, refresh, transport=None, **body) -> dict:
    """No job_id exists yet, so this can't go through a per-job CloudApiClient
    instance — a short-lived plain HTTP call instead. `transport` lets a
    caller with no live server (e.g. the `python -m portal crawl` debug CLI)
    hit the coordination routes in-process via httpx.ASGITransport instead of
    requiring uvicorn to already be running."""
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=15, transport=transport) as http:
        r = await _post_with_retry(http, "/api/coordination/jobs", token_provider, refresh, json=body)
        r.raise_for_status()
        return r.json()


async def resume_remote_job(base_url: str, token_provider, refresh, job_id: int, transport=None) -> dict:
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=15, transport=transport) as http:
        r = await _post_with_retry(http, f"/api/coordination/jobs/{job_id}/resume", token_provider, refresh)
        r.raise_for_status()
        return r.json()


class CloudApiClient:
    def __init__(self, base_url: str, token_provider, job_id: int, outbox_path, transport=None,
                cross_machine_resume: bool = False, refresh=None):
        self._base_url = base_url.rstrip("/")
        # Best-effort cache for the one sync call site (save_frontier, called
        # from a db_pool executor thread) — may be briefly stale, acceptable
        # since that upload already swallows failures (local checkpoint stays
        # authoritative regardless). Wrapping token_provider here means every
        # caller (including _post_with_retry/_get_with_retry) updates it for
        # free, with no extra call sites to remember.
        self._last_token: str | None = None

        async def _tracked_token_provider():
            self._last_token = await token_provider()
            return self._last_token

        self._token_provider = _tracked_token_provider
        self._refresh = refresh or (lambda: token_provider())
        self._job_id = job_id
        self._outbox = LocalOutbox(outbox_path)
        self._http = httpx.AsyncClient(base_url=self._base_url, timeout=15, transport=transport)
        self._flush_task: asyncio.Task | None = None
        self._cross_machine_resume = cross_machine_resume

    def start(self) -> None:
        self._flush_task = asyncio.create_task(self._flush_loop())

    # ── Direct calls (not outboxed) ──────────────────────────────────────────

    async def send_heartbeat(self, metrics: dict) -> bool:
        r = await _post_with_retry(self._http, f"/api/coordination/jobs/{self._job_id}/heartbeat",
                                   self._token_provider, self._refresh, json=metrics)
        r.raise_for_status()
        return bool(r.json().get("cancel_requested"))

    async def finish_job(self, status: str, error: str | None = None, drain_timeout: float = 30.0) -> None:
        deadline = time.monotonic() + drain_timeout
        while not self._outbox.is_drained(self._job_id) and time.monotonic() < deadline:
            await self._flush_kind("lead")
            await self._flush_kind("visited")
            if not self._outbox.is_drained(self._job_id):
                await asyncio.sleep(_FLUSH_BUSY_SLEEP)
        if not self._outbox.is_drained(self._job_id):
            log.warning(f"job {self._job_id}: outbox did not fully drain before finish "
                       f"(dead-lettered rows may exist) — finishing anyway")
        r = await _post_with_retry(self._http, f"/api/coordination/jobs/{self._job_id}/finish",
                                   self._token_provider, self._refresh, json={"status": status, "error": error})
        r.raise_for_status()

    # ── Outboxed writes (fire-and-forget, matches the old sync call shape) ──

    def save_lead(self, **fields) -> None:
        self._outbox.enqueue(self._job_id, "lead", fields)

    def mark_visited(self, url: str, job_id: int) -> None:
        self._outbox.enqueue(job_id, "visited", {"url": url})

    # ── Frontier checkpoint (survives a crash so a resume isn't a restart) ───

    def save_frontier(self, snapshot: dict) -> None:
        """Always writes the local checkpoint (fast, same-machine resume).
        When crawler.cross_machine_resume is on, also best-effort uploads to
        the cloud — called from a db_pool executor thread (see engine.py's
        `_save_checkpoint`), so a blocking sync HTTP call here doesn't block
        the event loop. A failed upload (including a stale cached token — no
        async refresh from a sync context) is logged and otherwise ignored:
        the local checkpoint is still authoritative for same-machine resume."""
        self._outbox.save_frontier(self._job_id, snapshot)
        if not self._cross_machine_resume:
            return
        if not self._last_token:
            log.warning(f"job {self._job_id}: no cached token yet — skipping cloud frontier upload")
            return
        try:
            with httpx.Client(base_url=self._base_url, timeout=15) as http:
                r = http.post(f"/api/coordination/jobs/{self._job_id}/frontier",
                              json={"snapshot": snapshot},
                              headers={"Authorization": f"Bearer {self._last_token}"})
                r.raise_for_status()
        except Exception as e:
            log.warning(f"job {self._job_id}: cloud frontier upload failed: {e}")

    async def load_frontier(self) -> dict | None:
        local = self._outbox.load_frontier(self._job_id)
        if local is not None or not self._cross_machine_resume:
            return local
        try:
            r = await _get_with_retry(self._http, f"/api/coordination/jobs/{self._job_id}/frontier",
                                      self._token_provider, self._refresh)
            r.raise_for_status()
            return r.json().get("snapshot")
        except Exception as e:
            log.warning(f"job {self._job_id}: cloud frontier fetch failed: {e}")
            return None

    def clear_frontier(self) -> None:
        self._outbox.clear_frontier(self._job_id)

    # ── Backpressure ─────────────────────────────────────────────────────────

    @property
    def is_backpressured(self) -> bool:
        """True once the LOCAL outbox backlog (across all jobs on this
        machine) exceeds a fixed threshold — a long cloud outage should slow
        new link discovery, not grow this file without bound."""
        return self._outbox.pending_count() > _BACKPRESSURE_THRESHOLD

    # ── Flusher ───────────────────────────────────────────────────────────────

    async def _flush_loop(self):
        try:
            while True:
                flushed_lead = await self._flush_kind("lead")
                flushed_visited = await self._flush_kind("visited")
                await asyncio.sleep(_FLUSH_BUSY_SLEEP if (flushed_lead or flushed_visited) else _FLUSH_IDLE_SLEEP)
        except asyncio.CancelledError:
            pass

    async def _flush_kind(self, kind: str) -> bool:
        batch = self._outbox.pending_batch(kind, limit=_BATCH_SIZE)
        if not batch:
            return False
        path = "leads" if kind == "lead" else "visited"
        body = {"items": [b["payload"] for b in batch]} if kind == "lead" else \
               {"urls": [b["payload"]["url"] for b in batch]}
        try:
            r = await _post_with_retry(self._http, f"/api/coordination/jobs/{self._job_id}/{path}",
                                       self._token_provider, self._refresh, json=body)
            r.raise_for_status()
            self._outbox.ack([b["id"] for b in batch])
            return True
        except Exception as e:
            log.warning(f"outbox flush ({kind}) failed for job {self._job_id}: {e}")
            for b in batch:
                self._outbox.fail(b["id"], b["job_id"], kind, b["payload"], str(e))
            await asyncio.sleep(1.0)
            return False

    async def best_effort_drain(self, timeout: float = 5.0) -> None:
        """Called on cancellation — a bounded attempt to flush before giving up,
        so a cancelled run doesn't strand more data than a crash would."""
        deadline = time.monotonic() + timeout
        while not self._outbox.is_drained(self._job_id) and time.monotonic() < deadline:
            await self._flush_kind("lead")
            await self._flush_kind("visited")

    async def aclose(self) -> None:
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self._http.aclose()
        self._outbox.close()
