"""Add owner_id to crawl_jobs/campaigns/test_campaigns

Revision ID: 0014_add_ownership
Revises: 0013_add_lookups_and_job_domains

Phase 2, chunk 1 (ownership + view filters). Existing rows predate
multi-user (Phase 0), so they're backfilled to the first-created
is_admin=true user rather than left NULL — an unowned row would be
invisible even to the admin who created it under a plain "my jobs" view.
Guarded with an inspector per the 0011-0013 precedent.
"""
import logging

import sqlalchemy as sa

from alembic import op

log = logging.getLogger(__name__)

revision = '0014_add_ownership'
down_revision = '0013_add_lookups_and_job_domains'
branch_labels = None
depends_on = None

_TABLES = ("crawl_jobs", "campaigns", "test_campaigns")


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table in _TABLES:
        if table not in inspector.get_table_names():
            continue
        columns = {c['name'] for c in inspector.get_columns(table)}
        if 'owner_id' not in columns:
            # Plain add_column() with an inline ForeignKey is NOT supported
            # by Alembic's SQLite dialect at all outside batch mode
            # ("No support for ALTER of constraints in SQLite dialect") — it
            # must go through batch_alter_table's copy-and-move recreate,
            # which in turn requires every constraint on the table to have
            # an explicit name (this table already carries other unnamed
            # FKs from earlier migrations). Postgres needs neither the batch
            # wrapper nor the naming_convention — this only matters on SQLite.
            with op.batch_alter_table(
                table,
                naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"},
            ) as batch_op:
                batch_op.add_column(sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id')))

    if 'users' not in inspector.get_table_names():
        return

    admin_row = bind.execute(sa.text(
        "SELECT id FROM users WHERE is_admin = :true ORDER BY id ASC LIMIT 1"
    ), {"true": True}).first()
    if not admin_row:
        log.info("0014_add_ownership: no admin user found yet, skipping owner_id backfill")
        return

    admin_id = admin_row[0]
    for table in _TABLES:
        if table not in inspector.get_table_names():
            continue
        bind.execute(sa.text(
            f"UPDATE {table} SET owner_id = :admin_id WHERE owner_id IS NULL"
        ), {"admin_id": admin_id})


def downgrade():
    for table in _TABLES:
        with op.batch_alter_table(
            table,
            naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"},
        ) as batch_op:
            batch_op.drop_column('owner_id')
