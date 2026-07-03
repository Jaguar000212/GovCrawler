"""Add template_id, created_at to campaigns and error_message, sent_at to campaign_emails"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0002_patch_campaign_fields"
down_revision = "0001_add_outreach_models"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "campaigns",
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("email_templates.id"), nullable=True),
    )
    op.add_column(
        "campaigns",
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "campaign_emails",
        sa.Column("error_message", sa.String(), nullable=True),
    )
    op.add_column(
        "campaign_emails",
        sa.Column("sent_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_column("campaign_emails", "sent_at")
    op.drop_column("campaign_emails", "error_message")
    op.drop_column("campaigns", "created_at")
    op.drop_column("campaigns", "template_id")
