"""Create least-privilege govcrawler_app Postgres role + audit_log grants

Revision ID: 0020_least_privilege_role
Revises: 0019_add_sending_status

Phase 5, chunk 3 (plan.md §13). Postgres previously ran under one
all-privileges role for both migrations and runtime traffic. This creates a
LOGIN role with no CREATEDB/CREATEROLE/superuser, grants it ordinary CRUD on
every app table, then explicitly revokes UPDATE/DELETE on audit_log (grants
only SELECT/INSERT) — the DB-grant-level append-only enforcement the
checklist asks for, since "the app role can't UPDATE/DELETE it" is a
stronger guarantee than "the app code doesn't call those methods."
ALTER DEFAULT PRIVILEGES extends the same grants to tables/sequences created
by future migrations (run by the same migrating role), so this doesn't need
re-running every time a new table is added.

Postgres-only — no equivalent concept on SQLite (desktop/dev installs), so
this is a no-op there. Requires GOVCRAWLER_APP_PASSWORD in the environment
the `migrate` service runs with; skips (with a warning) if unset, since a
fresh dev Postgres instance may not have it configured yet. See
deploy/SECURITY.md and deploy/docker-compose.yml (api/dispatcher's
DATABASE_URL_APP) for how the role is actually used at runtime.
"""
import logging
import os

import sqlalchemy as sa

from alembic import op

log = logging.getLogger(__name__)

revision = '0020_least_privilege_role'
down_revision = '0019_add_sending_status'
branch_labels = None
depends_on = None

_APP_ROLE = "govcrawler_app"


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return

    password = os.environ.get('GOVCRAWLER_APP_PASSWORD')
    if not password:
        log.warning(
            f"GOVCRAWLER_APP_PASSWORD not set — skipping {_APP_ROLE} creation/grants. "
            f"Set it and re-run 'alembic upgrade head' to enable the least-privilege runtime role."
        )
        return

    escaped = password.replace("'", "''")
    op.execute(sa.text(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                CREATE ROLE {_APP_ROLE} LOGIN PASSWORD '{escaped}';
            ELSE
                ALTER ROLE {_APP_ROLE} PASSWORD '{escaped}';
            END IF;
        END $$;
    """))

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {_APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE "
              f"ON TABLES TO {_APP_ROLE}")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {_APP_ROLE}")

    # audit_log append-only at the DB-grant level: no UPDATE/DELETE, ever.
    op.execute(f"REVOKE UPDATE, DELETE ON audit_log FROM {_APP_ROLE}")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    # Disruptive if DATABASE_URL_APP is actively in use by a running
    # api/dispatcher process — intentional; downgrading this migration means
    # you're reverting to the single-superuser-role model.
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT, INSERT, UPDATE, DELETE "
              f"ON TABLES FROM {_APP_ROLE}")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM {_APP_ROLE}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {_APP_ROLE}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {_APP_ROLE}")
    op.execute(sa.text(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                DROP ROLE {_APP_ROLE};
            END IF;
        END $$;
    """))
