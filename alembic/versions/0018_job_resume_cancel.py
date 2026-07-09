"""Add crawl_jobs.cancel_requested/agent_hostname/last_heartbeat_at

Revision ID: 0018_job_resume_cancel
Revises: 0017_encrypt_credentials

Phase 3, chunk 1 (coordination endpoints prerequisite). The engine currently
learns about cancellation only via in-memory asyncio.Task.cancel() — there is
no DB-polled signal a remote agent could observe. `cancel_requested` is the
signal a heartbeat response reads; `last_heartbeat_at` lets a future reaper
(Phase 4) detect a stalled agent; `agent_hostname` records which machine ran
the job. `JobStatus.INTERRUPTED` already exists in shared/enums.py (added
ahead of use) — status stays a plain String column, no CHECK to alter.
Guarded with an inspector per the 0011-0017 precedent.
"""
import sqlalchemy as sa

from alembic import op

revision = '0018_job_resume_cancel'
down_revision = '0017_encrypt_credentials'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'crawl_jobs' not in inspector.get_table_names():
        return

    columns = {c['name'] for c in inspector.get_columns('crawl_jobs')}
    # naming_convention as a precaution: crawl_jobs already has an unnamed
    # owner_id FK (0014) — if any of these three plain adds ever forces a
    # SQLite recreate, this avoids the "Constraint must have a name" error
    # 0014/0015/0016 hit for the same underlying reason.
    with op.batch_alter_table(
            'crawl_jobs',
            naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"},
    ) as batch_op:
        if 'cancel_requested' not in columns:
            batch_op.add_column(sa.Column('cancel_requested', sa.Boolean(), nullable=False,
                                          server_default=sa.false()))
        if 'agent_hostname' not in columns:
            batch_op.add_column(sa.Column('agent_hostname', sa.String(), nullable=True))
        if 'last_heartbeat_at' not in columns:
            batch_op.add_column(sa.Column('last_heartbeat_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table(
            'crawl_jobs',
            naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"},
    ) as batch_op:
        batch_op.drop_column('last_heartbeat_at')
        batch_op.drop_column('agent_hostname')
        batch_op.drop_column('cancel_requested')
