# API Reference

Two separate FastAPI apps now serve this system (plan.md §19.1 Phase 9 Part 2): the **cloud** app
(`cloud/api/`, on the VPS — everything in this doc unless marked otherwise) and each operator's **agent
BFF** (`agent/bff/`, loopback-only, one per machine). The agent renders every operator page itself and
proxies almost every call below straight through to the cloud with the operator's bearer token
(`agent/bff/proxy.py` — one generic reverse-proxy, not a route-by-route list); only the routes explicitly
marked **(agent-local)** have their own, different implementation on the agent side. The admin dashboard is
cloud-only and not reachable from the agent at all.

- **Auth (cloud)** — every `/api/*` router requires a valid session (`get_current_user`: `Authorization:
  Bearer <jwt>` header, or the `access` cookie for a direct cloud-side browser session — e.g. the admin
  dashboard) plus CSRF (`verify_csrf`). Mutating routes add a `require(<permission>)` check and write an
  `audit_log` row.
- **Auth (agent)** — every agent-side route requires loopback (`require_loopback`, checked against the
  actual peer address) plus a local session (`require_local_session`: the browser's own `session` cookie,
  established by the launcher's login) plus local CSRF (`verify_local_csrf`: double-submit **and** a
  trusted-`Host` check against DNS-rebinding). The agent forwards the operator's real bearer token upstream
  server-side — it never reaches the browser.
- **CSRF (cloud)** — double-submit: unsafe methods from a cookie session must send `X-CSRF-Token` matching
  the `csrf` cookie. Requests carrying a `Bearer` header are exempt (not CSRF-able) — this is also why the
  agent's proxied calls to the cloud need no CSRF handling of their own.
- **Ownership** — list/detail endpoints filter to the caller's own rows unless they hold the matching
  `*.view_all` permission (or are admin).
- **Permissions** in the tables below name the `require(...)` guard; "auth" means "any authenticated user".

See [authentication.md](authentication.md) for the token model and permission catalog.

---

## Auth — `cloud/api/auth.py`

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| POST | `/auth/login` | public | Verify email/password (argon2id); enforces lockout; issues access + refresh tokens, sets cookies; audits `user.login` |
| POST | `/auth/refresh` | public | Rotate refresh token (accepts it in the body or the `refresh` cookie); reuse of a revoked token revokes the whole session family |
| POST | `/auth/logout` | public | Revoke session (refresh token in the body or the `refresh` cookie); clears cookies |
| GET | `/auth/me` | auth | Current `UserOut` (id, email, is_admin, role, effective permissions) |

## Agent local auth — `agent/bff/local_auth.py` (agent-local, not proxied)

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| POST | `/auth/login` | loopback + local CSRF | Relays `{email, password}` to the cloud's real `/auth/login`; seeds `agent/identity.py`'s session cache and the browser's local `session`/`csrf` cookies. `frontend/login.html`'s existing JS calls this unmodified — mainly a fallback, since the launcher is the primary authenticator |
| GET | `/local-bootstrap` | loopback | Hands the browser a local session, once the launcher has already logged in — replaces the old cross-process `/auth/bootstrap?token=` hand-off, which no longer exists (there's no second process to bootstrap into) |
| POST | `/auth/logout` | loopback + local CSRF | Best-effort revokes the cloud session (keyring refresh token), clears `agent/identity.py`'s cache + keyring + local cookies |
| GET | `/auth/me` | loopback + local session | This machine's cached `{email, is_admin, permissions}` — no network round trip |

## Admin — `cloud/api/admin.py` (router-level `require("users.manage")`)

| Method | Path | Purpose |
|--------|------|---------|
| GET/POST | `/api/admin/users` | List / create users (409 on duplicate email) |
| GET | `/api/admin/users/{id}` | Detail: resolved effective permissions + raw per-permission overrides |
| PATCH | `/api/admin/users/{id}` | Set `is_active` and/or `role` |
| POST | `/api/admin/users/{id}/reset-password` | Set a new password |
| PUT | `/api/admin/users/{id}/permissions/{key}` | Grant/deny/clear one permission override on top of the user's role (`{"effect": "grant"\|"deny"\|null}`) |
| GET | `/api/admin/roles` | List the 3 built-in roles, each with its resolved `permissions` list — read-only, no create/edit-role endpoint exists |
| GET | `/api/admin/permissions` | The full permission catalog (key → description), for rendering the override grid |

## Audit log — `cloud/api/audit.py` (`require("audit.view")` — deliberately separate from `users.manage`)

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| GET | `/api/admin/audit` | `audit.view` | Paginated, filterable (`user_id`, `action_prefix`, `date_from`, `date_to`) audit trail |

## Settings (crawl policy) — `cloud/api/config.py`

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| GET | `/api/config` | auth | Flattened crawler + extraction + lead-score-weight settings |
| POST | `/api/config` | `settings.manage` | Update settings — machine-local keys persist to `config.yaml`, policy keys (incl. weights) persist to `app_settings` (plan.md §19.1 Phase 8); a weight change schedules a background lead-score recompute; audits `settings.update` |

## Domains — `cloud/api/domains.py`

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| GET | `/api/categories` | auth | Categories with counts |
| GET | `/api/states?category=` | auth | States, optionally category-filtered |
| GET | `/api/org-types?category=&state=` | auth | Org types filtered by category+state |
| GET | `/api/domains` | auth | Paginated catalog (filters, sort, page/limit ≤200) |
| GET | `/api/domains/ids` | auth | All matching crawlable domain IDs (select-all) |
| GET | `/api/domains/stats` | auth | `{total, crawlable, not_crawlable, duplicate}` |
| PATCH | `/api/domains/{id}` | `domains.import` | Set a "not crawlable" domain's `main_url`/`contact_url`; audits `domain.set_url` |

## Domain import — `cloud/api/imports.py` (single-flight)

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| POST | `/api/import/json` | `domains.import` | Upload `gov_domains.json`; background import (zero API calls); audits `domain.import_json` |
| POST | `/api/import` | `domains.import` | Background live import from india.gov.in; audits `domain.import_live` |
| GET | `/api/import/status` | auth | Poll import progress |

## Crawl jobs — read (`cloud/api/jobs.py`) + lifecycle (`agent/api.py`, agent-local)

Job **creation/resume/cancel** are the agent's own responsibility (they build the `CrawlerEngine` locally);
the cloud router only exposes reads (proxied through by the agent like everything else). These agent routes
check only loopback + local session + CSRF — the actual `crawl.run`/ownership authorization happens at
`cloud/api/coordination.py`, using the operator's own standing session (see below), not whichever request
reached this route.

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| POST | `/api/jobs` | agent-local (→ coordination checks `crawl.run`) | **(agent-local)** Create + start a crawl (domain_ids XOR custom_urls); stamps this agent's `agent_id` |
| POST | `/api/jobs/{id}/resume` | agent-local (→ coordination checks ownership + `crawl.run` + agent_id match) | **(agent-local)** Resume an interrupted job from its (local-only) frontier checkpoint — 403 if a different agent started it |
| POST | `/api/jobs/{id}/cancel` | agent-local (→ coordination checks ownership/`crawl.cancel_all`) | **(agent-local)** Cancel a running job (local task or via coordination) |
| GET | `/api/jobs?limit=` | auth | List recent jobs (owner-filtered unless `jobs.view_all`) — includes `agent_hostname` (the owning agent's id) |
| GET | `/api/jobs/{id}` | auth | Single job status + live metrics + `agent_hostname` |
| GET | `/api/jobs/{id}/seeds` | auth | Resolve seeds (custom URLs or frozen snapshots) |

## Agent coordination — `cloud/api/coordination.py` (prefix `/api/coordination`)

The contract a `CloudApiClient` speaks over the real network — authenticated as the operator's own
standing session (`agent/identity.py`). Writes on an **already-started** job (`leads`/`heartbeat`/`finish`)
authorize on **job ownership** only (the owner, or an admin; `/cancel` also accepts `crawl.cancel_all`) —
decoupled from the volatile `crawl.run` grant, so revoking a permission mid-crawl can't strand the outbox.
**Starting or resuming** a job (`/jobs`, `/jobs/{id}/resume`) additionally requires `crawl.run`. **Resuming**
also requires the caller's `agent_id` to match the job's — unconditionally, regardless of status/heartbeat
freshness, since a different agent has no frontier/visited data to resume from at all (plan.md §19.1
Phase 9 Part 2, judgment call #2). There is no `/visited` or `/frontier` route anymore — both are 100%
local to the agent that owns the job (`agent/localdb.py` / `agent/local_store.py`), never synced to the
cloud. See [resilience.md](resilience.md) for the durability guarantees.

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| POST | `/jobs` | `crawl.run` | Create job (stamps `agent_hostname` from the request's `agent_id`); freeze snapshots; return `{job_id, seeds, policy}` |
| POST | `/jobs/{id}/leads` | ownership | Batch lead upsert (enrich-dedup + score + occurrence) |
| POST | `/jobs/{id}/heartbeat` | ownership | Push metrics; returns `{cancel_requested}` |
| POST | `/jobs/{id}/finish` | ownership | Terminal status (`done`/`failed`/`cancelled`); audits `job.finish` |
| POST | `/jobs/{id}/cancel` | ownership or `crawl.cancel_all` | Set the cancel signal; audits `job.cancel` |
| POST | `/jobs/{id}/resume` | ownership + `crawl.run` + agent_id match | interrupted → running; rebuild seeds from custom URLs or snapshots; audits `job.resume` |

## Leads (shared pool) — `cloud/api/leads.py`

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| GET | `/api/leads` | auth | Paginated leads (rich filters: job/category/state/org_type/search/min_score/entry_type/require_*) |
| GET | `/api/leads/ids` | auth | All matching lead IDs (select-all) |
| GET | `/api/leads/score-weights` | auth | Current lead-score point weights |
| GET | `/api/leads/categories`·`/states`·`/org-types` | auth | Facet counts for the lead filters |
| POST | `/api/leads/export` | `leads.export` | CSV download (selectable field subset; email always included); audits `lead.export` |
| POST | `/api/leads/import-csv` | `leads.import` | Bulk create/update manual leads from CSV; audits `lead.import_csv` |
| GET | `/api/leads/import-csv/template` | auth | Downloadable CSV template |
| PUT | `/api/leads/{id}` | `leads.edit` | Edit name/designation/department/manual_state (400 `not_manual` if editing a crawled lead's state); audits `lead.update` |

## Campaigns — `cloud/api/campaigns.py`

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| POST | `/api/campaigns/parse-csv` | auth | Parse CSV → dummy details (no writes) |
| POST | `/api/campaigns` | `campaigns.manage` | Generate drafts (production from leads, test from dummy details); starts PAUSED; audits `campaign.create` |
| POST | `/api/campaigns/{id}/dispatch` | `campaigns.dispatch` | Start dispatch (embedded → spawn task; external → dispatcher picks it up); audits `campaign.dispatch` |
| GET | `/api/campaigns` | auth | Paginated list (+stats), owner-filtered unless `campaigns.view_all` |
| GET | `/api/campaigns/{id}` | auth | Detail + stats + assigned credential IDs |
| GET | `/api/campaigns/{id}/stats` | auth | Lightweight polling stats + status/pause_reason |
| GET | `/api/campaigns/{id}/emails` | auth | Paginated staged emails (status filter) |
| PATCH | `/api/campaigns/{id}` | `campaigns.dispatch` | Kill switch: pause/cancel; audits `campaign.set_status` |
| PUT | `/api/campaigns/{id}/credentials` | `campaigns.manage` | Change the SMTP credential pool; audits `campaign.set_credentials` |
| PUT | `/api/campaigns/{id}/emails/{eid}` | `campaigns.manage` | Manual subject/body override; audits `campaign.email_update` |
| PATCH | `/api/campaigns/{id}/emails/{eid}/selection` | `campaigns.manage` | Toggle one email |
| PATCH | `/api/campaigns/{id}/emails/selection-all` | `campaigns.manage` | Select/deselect all drafts |
| DELETE | `/api/campaigns/{id}/emails/{eid}` | `campaigns.manage` | Delete a DRAFT email; audits `campaign.email_delete` |
| POST | `/api/campaigns/{id}/emails` | `campaigns.manage` | Add leads to an existing production campaign; audits `campaign.add_emails` |

## Templates / Credentials / Blacklist

| Method | Path | Guard | Purpose |
|--------|------|-------|---------|
| GET | `/api/templates`·`/{id}` | auth | List / get email templates |
| POST/PUT/DELETE | `/api/templates[/{id}]` | `templates.manage` | Create/update (Jinja2-validated) / delete; audits `template.create`/`update`/`delete` |
| GET | `/api/credentials` | auth | List SMTP credentials (passwords masked; includes health) |
| POST/PUT/DELETE | `/api/credentials[/{id}]` | `credentials.manage` | CRUD (password Fernet-encrypted); audits `credential.create`/`update`/`delete` (password value never logged) |
| POST | `/api/credentials/{id}/test` | `credentials.manage` | Live SMTP connect+login test (auto-activate/disable); audits `credential.test` |
| GET | `/api/blacklist` | auth | Paginated blacklist |
| POST/DELETE | `/api/blacklist[/{id}]` | `blacklist.manage` | Block (domain auto-extracted) / unblock; audits `blacklist.add`/`remove` |

## Frontend pages

Rendered from three structurally separate trees under `frontend/` — see
[directory-structure.md](directory-structure.md) and [architecture.md](architecture.md#8-frontend--frontendsharedagentcloud).
The crawler/outreach pages are rendered only by **the agent** (`agent/bff/pages.py`, from `frontend/agent/`)
— the browser never talks to the cloud directly for these. The admin UI is rendered only by **the cloud**
(`cloud/api/frontend.py`, from `frontend/cloud/`) and is never mounted on the agent at all; an admin-capable
operator reaches it via an external link on the agent's dashboard (opens in a new tab, requiring its own
login). `/login` is the one page genuinely identical on both tiers (`frontend/shared/templates/login.html`).

| Path | Rendered by | Guard | Page |
|------|-------------|-------|------|
| `/login` | agent + cloud (same template) | public | Login |
| `/` | agent | local session | Dashboard (domains + job creation + status) |
| `/leads` | agent | local session | Leads browser |
| `/campaigns` | agent | local session | Campaigns |
| `/test-campaign` | agent | local session | Test campaign |
| `/settings` | agent | local session | Crawl-policy + outreach config editor |
| `/user-guide` | agent | local session | In-app crawler/outreach guide |
| `/` , `/admin/dashboard` | **cloud only** | `jobs.view_all` | Admin dashboard (3 s poll; Overview / Users & Permissions / Roles / Audit Log / System) |
| `/user-guide` | **cloud only** | auth | Admin-only guide (a different template than the agent's) |

## System & health

| Method | Path | Served by | Guard | Purpose |
|--------|------|-----------|-------|---------|
| GET | `/healthz` | cloud | public | Liveness/readiness (`SELECT 1`; 503 if DB unreachable) |
| GET | `/api/admin/activity` | cloud | `jobs.view_all` | Org-wide active jobs (DB-backed — `crawl_jobs.status`, not an in-process registry) + per-campaign dispatch stats + recently-finished tail |
| GET | `/api/admin/system-status` | cloud | `jobs.view_all` | Backs the admin dashboard's System tab: DB reachability, configured `dispatch.mode`, and a per-`agent_id` job-count/last-active summary derived from `crawl_jobs` |
| GET | `/api/system/activity` | **agent-local** | loopback + local session | This machine's own running crawl jobs (its local task registry — no campaign data, dispatch never runs here) |
| POST | `/api/system/cancel-all` | **agent-local** | loopback + local session + CSRF | Emergency stop — cancels this machine's own running jobs directly and best-effort signals the cloud |
| GET | `/api/logs` | **agent-local** | loopback + local session | This machine's own crawl log tail (last 1000 lines) — the VPS's server log is only visible from the cloud admin dashboard |
