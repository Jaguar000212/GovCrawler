"""Drop visited_urls and job_frontiers tables (both go 100% agent-local)

Revision ID: 0023_drop_visited_and_frontier
Revises: 0022_add_app_settings

Phase 9 Part 2, plan.md §19.1: per the user's explicit correction, visited-URL
history and frontier checkpoints never leave the originating machine anymore —
no cross-agent recrawl protection, no cross-machine resume (that was already
optional/off-by-default via crawler.cross_machine_resume, itself removed).
`agent/localdb.py` now holds the equivalent local-only data. `crawl_jobs`'
`visited_urls` INTEGER column (a plain progress counter reported via
heartbeat) is unrelated and untouched — only the `visited_urls` TABLE (the
actual list of URL strings) and `job_frontiers` are dropped.
"""
import sqlalchemy as sa

from alembic import op

revision = '0023_drop_visited_and_frontier'
down_revision = '0022_add_app_settings'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if 'visited_urls' in tables:
        op.drop_table('visited_urls')
    if 'job_frontiers' in tables:
        op.drop_table('job_frontiers')


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if 'job_frontiers' not in tables:
        op.create_table(
            'job_frontiers',
            sa.Column('job_id', sa.Integer(), sa.ForeignKey('crawl_jobs.id'), primary_key=True),
            sa.Column('snapshot_json', sa.Text(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
        )
    if 'visited_urls' not in tables:
        op.create_table(
            'visited_urls',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('url', sa.String(), nullable=False),
            sa.Column('job_id', sa.Integer(), sa.ForeignKey('crawl_jobs.id'), nullable=False),
            sa.Column('visited_at', sa.DateTime(), nullable=True),
            sa.UniqueConstraint('url', 'job_id', name='uq_visited_url_job'),
        )
        op.create_index('ix_visited_urls_url', 'visited_urls', ['url'])
