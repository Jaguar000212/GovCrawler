# Fault-injection acceptance pass (Phase 4)

Manual runbook — no automated test framework exists in this repo yet, so this
is a documented procedure, not a pytest suite. Goal: prove the Phase 4
guarantees actually hold (zero lead loss, exact resume, graceful backpressure)
rather than just trusting the code.

Prerequisites: a running `python -m portal serve`, at least one seed domain
with enough depth to still be crawling ~10s after start, and `sqlite3` on
PATH to inspect the local outbox file directly.

## 1. Crash mid-crawl, confirm the outbox + frontier survive

1. Start a crawl from the browser UI. Note the job id from the URL/response.
2. After ~10s (confirm via `GET /api/jobs/{id}` that `leads_found`/`visited_urls`
   are non-zero), kill the server process hard (`Ctrl+C` twice, or
   `taskkill /F` / `kill -9` — not a graceful shutdown).
3. Inspect `portal/data/outbox_job_<id>.db`:
   ```
   sqlite3 portal/data/outbox_job_<id>.db "select count(*) from outbox;"
   sqlite3 portal/data/outbox_job_<id>.db "select count(*) from frontier;"
   ```
   Expect `outbox` to have some pending rows (writes that hadn't flushed yet)
   and `frontier` to have exactly one row (the last 5s checkpoint) — both
   survived the kill because of `PRAGMA synchronous=FULL`.

## 2. Resume, confirm it continues rather than restarts

4. Restart the server (`python -m portal serve`). Within ~150s the stale-job
   reaper should flip the job's status to `interrupted` — confirm via
   `GET /api/jobs/{id}`.
5. `POST /api/jobs/{id}/resume`. Confirm the response says "Crawl resumed
   from checkpoint" (not "from seeds (no checkpoint found)").
6. Watch the leads/visited counts climb from where they left off, not from
   zero. If any seed had a pagination chain in progress, confirm (via logs —
   `_rehydrate_frontier`'s info log line reports chain count) that the chain
   count matches what was checkpointed, and that continued chain pages don't
   blow past `pagination.max_chain_children` (the whole point of the
   chain_budget aliasing fix — a naive restore would silently double the cap).

## 3. Network partition mid-crawl, confirm backpressure then recovery

7. Start a fresh crawl. Mid-crawl, edit `portal/config.yaml` to set
   `cloud_api_base_url: http://127.0.0.1:1` (an unreachable port) — this
   simulates the agent losing its connection to the cloud API without
   killing the crawl itself. (No live-reload exists, so this step is best
   validated by temporarily hardcoding the bad URL in `_cloud_base_url` for
   a manual test run, or by firewalling the real port briefly — pick
   whichever is easier in your environment.)
8. Confirm the crawl keeps running (worker logs still show fetches), the
   outbox file's `outbox` table grows without bound, and once past 5000
   pending rows, `_enqueue_links`'s debug log ("outbox backpressured —
   skipping link discovery") starts appearing — new leads/visited stop
   growing, but nothing already found disappears.
9. Restore the correct `cloud_api_base_url`. Confirm the flusher drains the
   backlog (outbox row count returns to near-zero) and, once the crawl
   finishes naturally, `finish_job` still succeeds.

## 4. Zero-loss check

10. Before step 2's kill in part 1, note `leads_found`/`visited_urls` from
    `GET /api/jobs/{id}`. After the resume in part 2 completes (job status
    `done`), compare the final counts. Some duplication across the crash
    point is expected and fine (dedup on the cloud side is idempotent via
    `ON CONFLICT`-style enrich); a net LOSS (final count lower than what was
    already confirmed flushed before the kill) is a real bug — if you see
    one, check `outbox_dead` for dead-lettered rows first (a poison payload
    that never made it, logged with its error) before concluding data was
    silently dropped elsewhere.
