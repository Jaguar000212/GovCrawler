# Directory Structure

Annotated file tree. Runtime artefacts (`__pycache__/`, `playwright_browsers/`, `portal/data/`,
`deploy/.env`, `deploy/wal_archive/`, `deploy/backups/`) and IDE folders are omitted.

```
GovCrawler/
│
├── run.py                     # Desktop entry point (PyInstaller target): SSL cert fix,
│                              # "INSTALL_BROWSERS" argv sentinel, no-console stdio guard,
│                              # then launches agent.launcher.app.CrawlerLauncher
├── GovCrawler.spec            # PyInstaller spec — bundles frontend, alembic, assets, config
├── alembic.ini                # Alembic config; env.py targets cloud.db.Base
├── pyproject.toml             # ruff + black (line-length 120, py311) + pytest + import-linter config
├── requirements.txt           # Desktop shim: -r requirements/cloud.txt + -r requirements/agent.txt
├── requirements/               # Per-tier pins — shared.txt, cloud.txt (+shared), agent.txt (+shared)
├── requirements-dev.txt       # -r requirements.txt + pytest/ruff/black
├── README.md
│
├── shared/                    # Framework-light; imported by BOTH cloud and agent, imports neither
│   ├── enums.py               # CampaignStatus, CampaignKind, EmailStatus, JobStatus
│   ├── permissions.py         # PERMISSIONS catalog (19 keys) + ROLE_DEFAULTS + BUILTIN_ROLES
│   ├── scoring.py             # compute_lead_score(), DEFAULT_WEIGHTS (pure function)
│   └── schemas/
│       └── auth.py            # Pydantic DTOs: LoginRequest, RefreshRequest, UserOut, TokenResponse
│
├── cloud/                     # THE VPS APP — FastAPI + Postgres, auth, admin, dispatcher
│   ├── api/
│   │   ├── server.py          # create_app(config, db): routers, lifespan (browser + reaper),
│   │   │                      #   CORS/CSRF, static mount, JWT-secret bootstrap
│   │   ├── deps.py            # get_current_user, require(), require_loopback, verify_csrf,
│   │   │                      #   CurrentUser, RedirectException, shared app state
│   │   ├── auth.py            # /auth/login|refresh|logout|me, /auth/bootstrap (loopback)
│   │   ├── admin.py           # /api/admin/users|roles (require users.manage)
│   │   ├── coordination.py    # /api/coordination/* — the agent↔cloud contract
│   │   ├── frontend.py        # HTML page routes (Jinja2) + /api/logs, DELETE /api/visited-urls
│   │   ├── system.py          # /healthz, /api/system/activity, /api/admin/activity, cancel-all
│   │   ├── config.py          # GET/POST /api/config — the crawl-policy "settings" router
│   │   ├── domains.py         # catalog browse + PATCH a no-URL domain's URL
│   │   ├── imports.py         # /api/import/json|/api/import|/api/import/status (single-flight)
│   │   ├── jobs.py            # read-only job list/detail/seeds (creation lives in agent/api.py)
│   │   ├── leads.py           # shared-pool browse/export/import-csv/edit
│   │   ├── campaigns.py       # campaign + email staging + dispatch routes
│   │   ├── dispatcher.py      # run_campaign_dispatch() SMTP loop (shared by both modes)
│   │   ├── credentials.py     # SMTP credential CRUD + live connection test
│   │   ├── templates.py       # Jinja2 email-template CRUD (validated)
│   │   └── blacklist.py       # email/domain blacklist CRUD
│   ├── db/
│   │   ├── base.py            # declarative_base() + SQLite WAL pragma listener
│   │   ├── database.py        # Database class, composed from the 7 mixins; _ensure_columns()
│   │   ├── enums.py           # re-export of shared.enums (import compat)
│   │   ├── migrations.py      # run_migrations(): stamp-then-upgrade on startup
│   │   ├── tables/            # auth.py, crawl.py, leads.py, lookups.py, outreach.py
│   │   └── mixins/            # auth, domain, job, crawl_snapshot, lead, visited, outreach
│   ├── security/
│   │   ├── hashing.py         # argon2id hash/verify/needs_rehash
│   │   ├── jwt.py             # HS256 access tokens + opaque refresh tokens
│   │   └── crypto.py          # Fernet credential encryption + key rotation (MultiFernet)
│   ├── services/
│   │   ├── campaign_service.py # render_draft_emails() — blacklist filter + Jinja2 + missing-field detect
│   │   ├── csv_import.py      # parse_contacts_csv(), build_template_csv()
│   │   └── importer.py        # import_from_json() / import_all() into the domains catalog (india.gov.in
│   │                          #   Web Directory API calls inlined here — see GovScraper/ below)
│   └── dispatch_service.py    # `python -m cloud.dispatch_service` — standalone (external) dispatcher
│
├── frontend/                   # SHARED UI — Jinja2 templates + vanilla JS/CSS (served by the cloud API)
│   ├── base.html               # layout + permission-gated nav (incl. 🛡️ Admin link)
│   ├── login.html
│   ├── index.html              # domains browser + crawl job creation + live status
│   ├── leads.html
│   ├── campaigns.html
│   ├── test-campaign.html
│   ├── admin-dashboard.html    # /admin/dashboard (require jobs.view_all), 3 s poll
│   ├── user-guide.html
│   └── static/{css,js,img}     # base + per-page assets; favicon
│
├── agent/                     # THE LOCAL APP (per machine) — crawler + BFF + launcher
│   ├── api.py                 # Local BFF: POST /api/jobs, /api/jobs/{id}/resume, .../cancel — zero
│   │                          #   cloud.* imports (Phase 9 Part 1); loopback-gated, uses identity.py
│   ├── identity.py            # The operator's standing session: self-refreshing token via /auth/refresh
│   │                          #   + OS keyring, decoupled from any one browser request
│   ├── state.py                # Agent-owned config/browser/active_tasks (replaces reading cloud.api.deps)
│   ├── cloud_client.py        # CloudApiClient + create_remote_job/resume_remote_job + outbox flusher —
│   │                          #   token_provider is async with retry-on-401-then-refresh
│   ├── local_store.py         # LocalOutbox: durable SQLite (outbox, outbox_dead, frontier)
│   ├── crawler/
│   │   ├── engine.py          # CrawlerEngine: priority queue, httpx/playwright, checkpoint, orchestration
│   │   ├── pagination.py      # Stateless pagination-link classifiers (is_pagination_link, safe_int, ...)
│   │   └── parser.py          # 6-stage lead-extraction pipeline + Lead dataclass + parse_for_engine
│   └── launcher/
│       ├── app.py             # CrawlerLauncher (AppState machine) + LoginDialog; keyring; drain shutdown
│       ├── tray.py            # TrayController (pystray)
│       └── notifications.py   # notify() — notifypy toasts (cross-platform)
│
├── portal/                    # Thin entry-point + config shim (NOT the old monolith)
│   ├── __main__.py            # `python -m portal` → portal.main.main()
│   ├── main.py                # load_config() (+ env overrides), CLI: serve/import/import-json/crawl/create-admin
│   ├── paths.py               # path resolution + first-run bootstrap (dev + PyInstaller frozen)
│   └── default_config.yaml    # shipped config template (config.yaml is the gitignored live copy)
│
├── GovScraper/                # Standalone dev-time CLI, fully decoupled from cloud/agent/shared — its
│   │                          # API-calling code is duplicated (inlined) into cloud/services/importer.py,
│   │                          # this package is only for regenerating gov_domains.json by hand
│   ├── runner.py              # CLI: `python runner.py [out.json] [--category] [--org-type]`
│   ├── README.md
│   └── api/                   # api.py, config.py, extractor.py, __init__.py, docs.md
│
├── alembic/
│   ├── env.py                 # targets `from cloud.db import Base`; honors DATABASE_URL env
│   └── versions/              # 0000_add_core_tables … 0021_add_job_frontier (chain head)
│
├── deploy/                    # Production VPS deployment
│   ├── docker-compose.yml     # db · migrate · api · dispatcher · proxy
│   ├── Dockerfile             # Playwright base image; one image for migrate/api/dispatcher
│   ├── Caddyfile              # reverse_proxy api:8001 + automatic TLS
│   ├── config.docker.yaml     # container-tuned config (baked to portal/config.yaml)
│   ├── .env.example           # secrets + env template
│   ├── SECURITY.md            # hardening checklist + rotation runbooks
│   ├── BACKUP.md              # daily pg_dump + rehearsed restore (RPO ≤24h)
│   ├── PITR.md                # WAL archiving + point-in-time recovery (RPO minutes)
│   ├── backup.sh · restore.sh · harden-vps.sh
│
├── scripts/
│   ├── migrate_sqlite_to_pg.py        # one-time SQLite→Postgres data migration (PK remap)
│   ├── rotate_credential_encryption_key.py  # re-encrypt SMTP creds under a new key
│   ├── generate_version_info.py       # PyInstaller Windows version resource from a git tag
│   └── fault_injection_check.md       # manual resilience acceptance runbook
│
├── tests/                     # Split by tier, mirroring shared/cloud/agent
│   ├── shared/                # test_imports.py (portal.main), test_config.py (env-override behavior)
│   ├── cloud/                 # test_imports.py (cloud.api.server)
│   └── agent/                 # test_imports.py (agent.api)
│
├── .github/workflows/
│   ├── ci.yaml                # lint (diff-scoped) · import-sanity · pytest · import-boundaries · migration smoke test
│   └── release.yaml           # tag-triggered PyInstaller build/release (win/mac/linux)
│
├── assets/favicon.ico
└── .docs/                     # This documentation tree
```

## Where the old `portal/` code went

The pre-overhaul monolith lived entirely under `portal/`. It was split by tier:

| Old location | New location |
|--------------|--------------|
| `portal/api/*` (data routers) | `cloud/api/*` |
| `portal/api/jobs.py` (job creation) | `agent/api.py` (local BFF) |
| `portal/db/*` | `cloud/db/*` |
| `portal/crawler/*` | `agent/crawler/*` |
| `portal/services/lead_scoring.py` | `shared/scoring.py` |
| `portal/db/enums.py` | `shared/enums.py` (re-exported by `cloud/db/enums.py`) |
| `launcher/` (repo root) | `agent/launcher/` |
| `portal/frontend/` | `frontend/` (hoisted from `cloud/frontend/` in Phase 7, plan.md §19.1) |
| `cloud/scraper/importer.py` | `cloud/services/importer.py` (Phase 7 — GovScraper's live-API calls inlined) |
| `portal/main.py`, `portal/paths.py` | unchanged (the surviving shim) |

## Generated / ignored paths

| Path | Why excluded from git |
|------|-----------------------|
| `portal/data/govcrawler.db` | Runtime SQLite DB (desktop/dev) |
| `portal/data/outbox_job_*.db` | Per-job durable outbox |
| `portal/data/portal.log` | Runtime log |
| `portal/config.yaml` | User-edited live config |
| `playwright_browsers/` | ~600 MB Chromium |
| `deploy/.env`, `deploy/backups/`, `deploy/wal_archive/` | Secrets + backup artefacts |
| `dist/`, `build/`, `**/__pycache__/`, `venv/` | Build/temp/env |
