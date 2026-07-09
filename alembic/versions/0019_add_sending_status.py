"""Add EmailStatus.SENDING + campaign_emails.sending_since (at-most-once send recovery)

Revision ID: 0019_add_sending_status
Revises: 0018_job_resume_cancel

Phase 5, chunk 1. The dispatcher (cloud/api/dispatcher.py) used to read a
QUEUED row, send it, and only then write SENT/FAILED — a crash between a
successful SMTP send and that write left the row QUEUED forever, so a
restart resent it. SENDING + sending_since let claim_next_queued_email()
atomically claim a row before sending (outreach_mixin.py), and
recover_stuck_sending() requeue anything left SENDING past a threshold
(mirrors job_mixin.reap_stale_jobs' non-destructive-revive shape).

emailstatus is a native Postgres enum (created in 0001) but a CHECK-
constrained VARCHAR on SQLite (SQLAlchemy's portable fallback for dialects
without native enum support) — the two backends need different DDL to add a
value, hence the dialect branch. Postgres 12+ allows ALTER TYPE ... ADD VALUE
inside a transaction. naming_convention on the SQLite batch recreate is
required: campaign_emails already carries unnamed FKs (campaign_id/lead_id/
credential_id) from earlier migrations, same precaution as 0016.
"""
import sqlalchemy as sa

from alembic import op

revision = '0019_add_sending_status'
down_revision = '0018_job_resume_cancel'
branch_labels = None
depends_on = None

_fk_naming = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'campaign_emails' not in inspector.get_table_names():
        return

    columns = {c['name'] for c in inspector.get_columns('campaign_emails')}

    if bind.dialect.name == 'postgresql':
        op.execute("ALTER TYPE emailstatus ADD VALUE IF NOT EXISTS 'SENDING'")
        if 'sending_since' not in columns:
            op.add_column('campaign_emails', sa.Column('sending_since', sa.DateTime(), nullable=True))
    else:
        with op.batch_alter_table('campaign_emails', naming_convention=_fk_naming) as batch_op:
            batch_op.alter_column(
                'status',
                existing_type=sa.Enum('DRAFT', 'QUEUED', 'SENT', 'FAILED', name='emailstatus'),
                type_=sa.Enum('DRAFT', 'QUEUED', 'SENDING', 'SENT', 'FAILED', name='emailstatus'),
            )
            if 'sending_since' not in columns:
                batch_op.add_column(sa.Column('sending_since', sa.DateTime(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'campaign_emails' not in inspector.get_table_names():
        return

    if bind.dialect.name == 'postgresql':
        # Postgres cannot drop an enum value — the 'SENDING' label stays
        # permanently. Harmless: nothing writes it once this migration is
        # reverted, since the claim/recover logic is app-level code, not a
        # DB constraint.
        op.drop_column('campaign_emails', 'sending_since')
    else:
        with op.batch_alter_table('campaign_emails', naming_convention=_fk_naming) as batch_op:
            batch_op.drop_column('sending_since')
            batch_op.alter_column(
                'status',
                existing_type=sa.Enum('DRAFT', 'QUEUED', 'SENDING', 'SENT', 'FAILED', name='emailstatus'),
                type_=sa.Enum('DRAFT', 'QUEUED', 'SENT', 'FAILED', name='emailstatus'),
            )
