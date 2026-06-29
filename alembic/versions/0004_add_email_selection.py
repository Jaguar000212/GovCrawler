"""Add is_selected and missing_fields to campaign_emails

Revision ID: 0004_add_email_selection
Revises: 0003_add_test_campaign_models
"""
from alembic import op
import sqlalchemy as sa

revision = '0004_add_email_selection'
down_revision = '0003_add_test_campaign_models'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'campaign_emails',
        sa.Column('is_selected', sa.Boolean(), nullable=False, server_default='1')
    )
    op.add_column(
        'campaign_emails',
        sa.Column('missing_fields', sa.String(), nullable=True)
    )
    op.add_column(
        'test_campaign_emails',
        sa.Column('is_selected', sa.Boolean(), nullable=False, server_default='1')
    )
    op.add_column(
        'test_campaign_emails',
        sa.Column('missing_fields', sa.String(), nullable=True)
    )


def downgrade():
    op.drop_column('test_campaign_emails', 'missing_fields')
    op.drop_column('test_campaign_emails', 'is_selected')
    op.drop_column('campaign_emails', 'missing_fields')
    op.drop_column('campaign_emails', 'is_selected')
