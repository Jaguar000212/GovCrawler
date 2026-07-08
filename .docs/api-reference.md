# API Reference

All endpoints are served by the cloud FastAPI app (`cloud/api/`), except the three job-lifecycle routes
which live in the agent BFF (`agent/api.py`) but are mounted on the same app. Conventions:

- **Auth** — every `/api/*` router requires a valid session (`get_current_user`: `Authorization: Bearer
  <jwt>` header, or the `access` cookie for the browser) plus CSRF (`verify_csrf`). Mutating routes add a
  `require(<permission>)` check and write an `audit_log` row.
- **CSRF** — double-submit: unsafe methods from a cookie session must send `X-CSRF-Token` matching the
  `csrf` cookie. Requests carrying a `Bearer` header are exempt (not CSRF-able).
- **Ownership** — list/detail endpoints filter to the caller's own rows unless they hold the matching
  `*.view_all` permission (or are admin).
- **Permissions** in the tables below name the `require(...)` guard; "auth" means "any authenticated user".

See [authentication.md](authentication.md) for the token model and permission catalog.

---

## Auth — `cloud/api/auth.py`

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| POST | `/auth/login` | public | Verify email/password (argon2id); enforces lockout; issues access + refresh tokens, sets cookies; audits `user.login` |
| POST | `/auth/refresh` | public | Rotate refresh token; reuse of a revoked token revokes the whole session family |
| POST | `/auth/logout` | public | Revoke session by refresh cookie; clears cookies |
| GET | `/auth/me` | auth | Current `UserOut` (id, email, is_admin, role, effective permissions) |
| GET | `/auth/bootstrap?token=` | loopback only | Launcher hands the browser its session (sets cookie, redirects to `/`) — avoids a second login |

## Admin — `cloud/api/admin.py` (router-level `require("users.manage")`)

| Method | Path | Purpose |
|--------|------|---------|
| GET/POST | `/api/admin/users` | List / create users (409 on duplicate email) |
| PATCH | `/api/admin/users/{id}` | Set `is_active` and/or `role` |
| POST | `/api/admin/users/{id}/reset-password` | Set a new password |
| GET | `/api/admin/roles` | List roles |

## Settings (crawl policy) — `cloud/api/config.py`

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| GET | `/api/config` | auth | Flattened crawler + extraction + lead-score-weight settings |
| POST | `/api/config` | `settings.manage` | Update settings — machine-local keys persist to `config.yaml`, policy keys (incl. weights) persist to `app_settings` (plan.md §19.1 Phase 8); a weight change schedules a background lead-score recompute |

## Domains — `cloud/api/domains.py`

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| GET | `/api/categories` | auth | Categories with counts |
| GET | `/api/states?category=` | auth | States, optionally category-filtered |
| GET | `/api/org-types?category=&state=` | auth | Org types filtered by category+state |
| GET | `/api/domains` | auth | Paginated catalog (filters, sort, page/limit ≤200) |
| GET | `/api/domains/ids` | auth | All matching crawlable domain IDs (select-all) |
| GET | `/api/domains/stats` | auth | `{total, crawlable, not_crawlable, duplicate}` |
| PATCH | `/api/domains/{id}` | `domains.import` | Set a "not crawlable" domain's `main_url`/`contact_url` |

## Domain import — `cloud/api/imports.py` (single-flight)

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| POST | `/api/import/json` | `domains.import` | Upload `gov_domains.json`; background import (zero API calls) |
| POST | `/api/import` | `domains.import` | Background live import from india.gov.in |
| GET | `/api/import/status` | auth | Poll import progress |

## Crawl jobs — read (`cloud/api/jobs.py`) + lifecycle (`agent/api.py`)

Job **creation/resume/cancel** are the agent BFF's responsibility (they build the `CrawlerEngine`); the
cloud router only exposes reads. As of Phase 9 Part 1, these routes themselves check only that the caller
is loopback (any-authenticated-user is still enforced one layer up, at the router mount in
`cloud/api/server.py`) — the actual `crawl.run`/ownership authorization happens at
`cloud/api/coordination.py`, using the operator's own standing session, not whichever token reached this
route (see [api-reference.md](#agent-coordination--cloudapicoordinationpy-prefix-apicoordination) below).

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| POST | `/api/jobs` | auth + loopback (→ coordination checks `crawl.run`) | **(agent)** Create + start a crawl (domain_ids XOR custom_urls) |
| POST | `/api/jobs/{id}/resume` | auth + loopback (→ coordination checks ownership + `crawl.run`) | **(agent)** Resume an interrupted job from its frontier checkpoint |
| POST | `/api/jobs/{id}/cancel` | auth + loopback (→ coordination checks ownership/`crawl.cancel_all`) | **(agent)** Cancel a running job (local or via coordination) |
| GET | `/api/jobs?limit=` | auth | List recent jobs (owner-filtered unless `jobs.view_all`) |
| GET | `/api/jobs/{id}` | auth | Single job status + live metrics |
| GET | `/api/jobs/{id}/seeds` | auth | Resolve seeds (custom URLs or frozen snapshots) |

## Agent coordination — `cloud/api/coordination.py` (prefix `/api/coordination`)

The contract a `CloudApiClient` speaks — as of Phase 9 Part 1 (plan.md §19.1), authenticated as the
operator's own standing session (`agent/identity.py`), not a per-request browser token; `agent/api.py`
itself no longer checks any permission locally (it only gates on loopback), so **every** check below
happens here now. Writes on an **already-started** job (`leads`/`visited`/`heartbeat`/`frontier`/`finish`)
authorize on **job ownership** only (the owner, or an admin; `/cancel` also accepts `crawl.cancel_all`) —
decoupled from the volatile `crawl.run` grant, so revoking a permission mid-crawl can't strand the outbox.
**Starting or resuming** a job (`/jobs`, `/jobs/{id}/resume`) additionally requires `crawl.run` — that's a
deliberate user action, not an in-flight write, so it's fine (correct, even) for a revoked grant to block it.
See [resilience.md](resilience.md) for the durability guarantees.

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| POST | `/jobs` | `crawl.run` | Create job; freeze snapshots; return `{job_id, seeds, policy, visited_bootstrap}` |
| POST | `/jobs/{id}/leads` | ownership | Batch lead upsert (enrich-dedup + score + occurrence) |
| POST | `/jobs/{id}/visited` | ownership | Batch visited-URL mark (idempotent) |
| POST | `/jobs/{id}/heartbeat` | ownership | Push metrics; returns `{cancel_requested}` |
| POST/GET | `/jobs/{id}/frontier` | ownership | Save / load the frontier snapshot (cross-machine resume) |
| POST | `/jobs/{id}/finish` | ownership | Terminal status (`done`/`failed`/`cancelled`) |
| POST | `/jobs/{id}/cancel` | ownership or `crawl.cancel_all` | Set the cancel signal |
| POST | `/jobs/{id}/resume` | ownership + `crawl.run` | interrupted → running; rebuild seeds from custom URLs or snapshots |

## Leads (shared pool) — `cloud/api/leads.py`

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| GET | `/api/leads` | auth | Paginated leads (rich filters: job/category/state/org_type/search/min_score/entry_type/require_*) |
| GET | `/api/leads/ids` | auth | All matching lead IDs (select-all) |
| GET | `/api/leads/score-weights` | auth | Current lead-score point weights |
| GET | `/api/leads/categories`·`/states`·`/org-types` | auth | Facet counts for the lead filters |
| POST | `/api/leads/export` | `leads.export` | CSV download (selectable field subset; email always included) |
| POST | `/api/leads/import-csv` | `leads.import` | Bulk create/update manual leads from CSV |
| GET | `/api/leads/import-csv/template` | auth | Downloadable CSV template |
| PUT | `/api/leads/{id}` | `leads.edit` | Edit name/designation/department/manual_state (400 `not_manual` if editing a crawled lead's state) |

## Campaigns — `cloud/api/campaigns.py`

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| POST | `/api/campaigns/parse-csv` | auth | Parse CSV → dummy details (no writes) |
| POST | `/api/campaigns` | `campaigns.manage` | Generate drafts (production from leads, test from dummy details); starts PAUSED |
| POST | `/api/campaigns/{id}/dispatch` | `campaigns.dispatch` | Start dispatch (embedded → spawn task; external → dispatcher picks it up) |
| GET | `/api/campaigns` | auth | Paginated list (+stats), owner-filtered unless `campaigns.view_all` |
| GET | `/api/campaigns/{id}` | auth | Detail + stats + assigned credential IDs |
| GET | `/api/campaigns/{id}/stats` | auth | Lightweight polling stats + status/pause_reason |
| GET | `/api/campaigns/{id}/emails` | auth | Paginated staged emails (status filter) |
| PATCH | `/api/campaigns/{id}` | `campaigns.dispatch` | Kill switch: pause/cancel |
| PUT | `/api/campaigns/{id}/credentials` | `campaigns.manage` | Change the SMTP credential pool |
| PUT | `/api/campaigns/{id}/emails/{eid}` | `campaigns.manage` | Manual subject/body override |
| PATCH | `/api/campaigns/{id}/emails/{eid}/selection` | `campaigns.manage` | Toggle one email |
| PATCH | `/api/campaigns/{id}/emails/selection-all` | `campaigns.manage` | Select/deselect all drafts |
| DELETE | `/api/campaigns/{id}/emails/{eid}` | `campaigns.manage` | Delete a DRAFT email |
| POST | `/api/campaigns/{id}/emails` | `campaigns.manage` | Add leads to an existing production campaign |

## Templates / Credentials / Blacklist

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| GET | `/api/templates`·`/{id}` | auth | List / get email templates |
| POST/PUT/DELETE | `/api/templates[/{id}]` | `templates.manage` | Create/update (Jinja2-validated) / delete |
| GET | `/api/credentials` | auth | List SMTP credentials (passwords masked; includes health) |
| POST/PUT/DELETE | `/api/credentials[/{id}]` | `credentials.manage` | CRUD (password Fernet-encrypted) |
| POST | `/api/credentials/{id}/test` | `credentials.manage` | Live SMTP connect+login test (auto-activate/disable) |
| GET | `/api/blacklist` | auth | Paginated blacklist |
| POST/DELETE | `/api/blacklist[/{id}]` | `blacklist.manage` | Block (domain auto-extracted) / unblock |

## Frontend pages — `cloud/api/frontend.py`

HTML routes rendered from the top-level `frontend/` directory. Unauthenticated access redirects to `/login`.

| Path | Guard | Page |
|------|-------|------|
| `/login` | public | Login |
| `/` | auth | Dashboard (domains + job creation + status) |
| `/leads` | auth | Leads browser |
| `/campaigns` | auth | Campaigns |
| `/test-campaign` | auth | Test campaign |
| `/settings` | auth | Crawl-policy editor |
| `/admin/dashboard` | `jobs.view_all` | Admin real-time dashboard (3 s poll) |
| `/user-guide` | auth | In-app guide |
| `GET /api/logs` | auth | Last 1000 log lines |
| `DELETE /api/visited-urls` | `crawl.run` | Clear the visited-URL table |

## System & health — `cloud/api/system.py`

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| GET | `/healthz` | public | Liveness/readiness (`SELECT 1`; 503 if DB unreachable) |
| GET | `/api/system/activity` | loopback only | Active jobs/campaigns for the Tkinter launcher |
| GET | `/api/admin/activity` | `jobs.view_all` | Active + per-campaign stats + recently-finished tail (admin dashboard) |
| POST | `/api/system/cancel-all` | loopback only | Emergency stop — cancel every active job and campaign |
