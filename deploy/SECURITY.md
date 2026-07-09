# Security checklist & runbooks (Phase 5, plan.md §13)

## Checklist status

- [x] Postgres localhost-only, never public — `db` service only exposes `127.0.0.1:5432` (host-side, for the one-time
  migration script), everything else reaches it over the internal compose network.
- [x] TLS everywhere — Caddy (`deploy/Caddyfile`) auto-provisions TLS for `${DOMAIN}`.
- [x] argon2id passwords, never logged; login rate-limited + lockout — `cloud/security/hashing.py`, `cloud/api/auth.py`.
- [x] JWT secret + `CREDENTIAL_ENC_KEY` in env, **with a rotation path** — see below.
- [x] SMTP passwords encrypted at rest, decrypted only in the dispatcher — `cloud/security/crypto.py`.
- [x] RBAC enforced server-side; ownership filters in queries.
- [x] Refresh-token revocation (`user_sessions` + `token_version`); rotated-token-reuse detection — `cloud/api/auth.py`.
- [x] CORS locked to the admin origin — `ADMIN_ORIGIN` env, `cloud/api/server.py`.
- [x] CSRF token on mutating BFF requests — double-submit `csrf` cookie, `cloud/api/deps.py:verify_csrf`.
- [x] Pydantic validation; SQLAlchemy ORM (parameterized) — no raw string SQL anywhere in route handlers.
- [x] `audit_log` append-only at the DB-grant level — Alembic `0020_least_privilege_role`.
- [x] Least-privilege Postgres role; ufw default-deny; SSH key-only; auto security updates — see below.
- [ ] Formal SOC 2 / ISO 27001 — explicitly out of scope (plan.md §13); the primitives above are in place if pursued
  later.
- [ ] Full metrics/alerting stack (Prometheus/Grafana) — out of scope this pass; `/healthz` (below) covers basic
  liveness. Candidate for Phase 6.

## Least-privilege Postgres role

Alembic migration `0020_least_privilege_role` creates a `govcrawler_app` role (ordinary
LOGIN, no CREATEDB/CREATEROLE/superuser) with SELECT/INSERT/UPDATE/DELETE on every table
except `audit_log` (SELECT/INSERT only — no UPDATE/DELETE, ever). `api`/`dispatcher`
connect at runtime via `DATABASE_URL_APP`; `migrate` keeps using the original
(superuser-ish) `DATABASE_URL` since it needs DDL rights.

Setup:

1. Set `GOVCRAWLER_APP_PASSWORD` and `DATABASE_URL_APP` in `deploy/.env` (see `.env.example`).
2. `docker compose -f deploy/docker-compose.yml up --build migrate` — creates/updates the role.
3. `docker compose -f deploy/docker-compose.yml up --build api dispatcher`.

If `GOVCRAWLER_APP_PASSWORD` is unset, migration 0020 skips role creation with a warning
and `api`/`dispatcher` fall back to `DATABASE_URL` (the old single-role behavior) — safe
default for a fresh dev Postgres, but fill it in before going to production.

## JWT secret rotation

Access tokens are short-lived (`auth.access_ttl_minutes`, default 15) and refresh tokens
are opaque random values hashed server-side (not JWTs) — rotating `JWT_SECRET` only
affects currently-live access tokens, not refresh sessions.

1. Set `JWT_SECRET_PREV=<current JWT_SECRET>` and generate a new `JWT_SECRET`
   (`python -c "import secrets; print(secrets.token_urlsafe(48))"`).
2. Redeploy `api`. `deps.decode_token_with_rotation` tries the new secret first, then
   `jwt_secret_prev` — sessions signed under the old secret keep working until they expire
   naturally (≤`access_ttl_minutes` later) or the holder refreshes (which re-signs under
   the new secret immediately).
3. After `access_ttl_minutes` has fully elapsed, drop `JWT_SECRET_PREV` and redeploy again.

## Credential encryption key rotation

SMTP credential passwords are encrypted with `CREDENTIAL_ENC_KEY` (Fernet). Losing every
key in the chain makes stored credentials permanently undecryptable — there is no recovery
path, by design.

1. Generate a new key (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
2. Set `CREDENTIAL_ENC_KEY_PREV=<old key>`, `CREDENTIAL_ENC_KEY=<new key>`, redeploy `api`
   and `dispatcher` (both need both keys to decrypt old rows while mid-rotation).
3. Run `python scripts/rotate_credential_encryption_key.py <DATABASE_URL>` — re-encrypts
   every stored credential under the new primary key.
4. Drop `CREDENTIAL_ENC_KEY_PREV`, redeploy again.

## OS-level hardening (ufw / SSH / auto-updates)

No test framework or VPS access exists in this repo to script this end-to-end safely, so
this is a run-once script + checklist rather than something docker-compose enforces:

```
scp deploy/harden-vps.sh you@your-vps:
ssh you@your-vps
sudo ./harden-vps.sh
```

Verify you can still SSH in via key **before closing that session** — the script disables
password auth. It: enables `ufw` (default-deny incoming, allows 22/80/443 only), disables
SSH password authentication (key-only), and enables `unattended-upgrades`.

## CORS / CSRF notes

- Caddy (`deploy/Caddyfile`) serves the frontend and API from one origin, so CORS is
  defense-in-depth here, not load-bearing. Set `ADMIN_ORIGIN` (derived from `DOMAIN`) if a
  separate frontend origin is ever introduced.
- CSRF uses a double-submit cookie: `auth.py` sets a non-`httponly` `csrf` cookie alongside
  the `access`/`refresh` cookies; `base.js` patches `window.fetch` globally to echo it back
  as `X-CSRF-Token` on every non-GET request; `deps.verify_csrf` checks the two match. Only
  enforced for cookie-authenticated requests — a request carrying `Authorization: Bearer`
  (the launcher, the crawler agent) is exempt, since that header isn't sent automatically
  by a browser and therefore isn't CSRF-able.
