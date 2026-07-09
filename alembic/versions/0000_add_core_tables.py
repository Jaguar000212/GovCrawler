"""Add core tables (domains, crawl_jobs, leads, visited_urls)

Revision ID: 0000_add_core_tables
Revises: (new root)

These four tables were never given an Alembic `create_table` — they only
ever existed via `Base.metadata.create_all()`, which every existing SQLite
install ran before `run_migrations()` ever executed (and `run_migrations()`
then just stamps "head" on a DB whose `alembic_version` table doesn't exist
yet, rather than replaying history — see `portal/db/migrations.py`). So this
gap was invisible until now: the Docker `migrate` service is the first time
`alembic upgrade head` has ever run against a truly empty database, with no
prior `create_all()` to paper over it.

Shape here is deliberately the ORIGINAL/minimal one — i.e. without the
columns that later revisions (0005_add_lead_depth, 0005_add_lead_grading,
0006_add_job_custom_urls, 0007_add_domain_external_id, 0010_add_lead_score)
add via `op.add_column`, so those migrations still have a column to add
instead of colliding with one already present. `crawl_jobs.current_depth`/
`active_workers` and `leads.snapshot_id` are intentionally absent from both
this baseline and the rest of the Alembic chain — they've only ever been
added via `Database._ensure_columns()` (see its docstring / 0011's), which
runs whenever `Database(config)` is constructed (i.e. when the `api`
service starts, right after this migration chain finishes).

Guarded with an inspector per the 0011/0012/0013 precedent, since
`Database.__init__` runs `create_all()` before Alembic on every existing
SQLite install.
"""
import sqlalchemy as sa

from alembic import op

revision = '0000_add_core_tables'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'domains' not in tables:
        op.create_table(
            'domains',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('category_code', sa.String(), nullable=False),
            sa.Column('category_title', sa.String(), nullable=True),
            sa.Column('state', sa.String(), nullable=True),
            sa.Column('org_type', sa.String(), nullable=True),
            sa.Column('org_type_title', sa.String(), nullable=True),
            sa.Column('title', sa.String(), nullable=True),
            sa.Column('main_url', sa.String(), nullable=True),
            sa.Column('contact_url', sa.String(), nullable=True),
            sa.Column('imported_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_domains_category_code', 'domains', ['category_code'])
        op.create_index('ix_domains_state', 'domains', ['state'])
        op.create_index('ix_domains_org_type', 'domains', ['org_type'])
        op.create_index('ix_domains_title', 'domains', ['title'])

    if 'crawl_jobs' not in tables:
        op.create_table(
            'crawl_jobs',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('category_filter', sa.String(), nullable=True),
            sa.Column('title_filter', sa.String(), nullable=True),
            sa.Column('domain_ids', sa.Text(), nullable=True),
            sa.Column('status', sa.String(), nullable=True, server_default='pending'),
            sa.Column('total_domains', sa.Integer(), nullable=True, server_default='0'),
            sa.Column('crawled_domains', sa.Integer(), nullable=True, server_default='0'),
            sa.Column('seed_domains', sa.Integer(), nullable=True, server_default='0'),
            sa.Column('queued_urls', sa.Integer(), nullable=True, server_default='0'),
            sa.Column('visited_urls', sa.Integer(), nullable=True, server_default='0'),
            sa.Column('skipped_urls', sa.Integer(), nullable=True, server_default='0'),
            sa.Column('leads_found', sa.Integer(), nullable=True, server_default='0'),
            sa.Column('error_message', sa.String(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('started_at', sa.DateTime(), nullable=True),
            sa.Column('finished_at', sa.DateTime(), nullable=True),
        )

    if 'leads' not in tables:
        op.create_table(
            'leads',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('job_id', sa.Integer(), sa.ForeignKey('crawl_jobs.id'), nullable=False),
            sa.Column('domain_id', sa.Integer(), sa.ForeignKey('domains.id'), nullable=True),
            sa.Column('email', sa.String(), nullable=False),
            sa.Column('person_name', sa.String(), nullable=True),
            sa.Column('designation', sa.String(), nullable=True),
            sa.Column('department', sa.String(), nullable=True),
            sa.Column('source_url', sa.String(), nullable=True),
            sa.Column('source_title', sa.String(), nullable=True),
            sa.Column('context_snippet', sa.Text(), nullable=True),
            sa.Column('domain_state', sa.String(), nullable=True),
            sa.Column('domain_org_type', sa.String(), nullable=True),
            sa.Column('captured_at', sa.DateTime(), nullable=True),
            sa.UniqueConstraint('job_id', 'email', name='uq_lead_job_email'),
        )
        op.create_index('ix_leads_job_id', 'leads', ['job_id'])
        op.create_index('ix_leads_email', 'leads', ['email'])

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


def downgrade():
    op.drop_table('visited_urls')
    op.drop_table('leads')
    op.drop_table('crawl_jobs')
    op.drop_table('domains')
