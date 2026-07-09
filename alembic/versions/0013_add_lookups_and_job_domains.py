"""Add categories/org_types lookup tables + crawl_job_domains junction

Revision ID: 0013_add_lookups_and_job_domains
Revises: 0012_add_auth

Phase 1 (partial normalization). New tables only — `categories`/`org_types`
are write-time seed targets (domains/crawl_snapshots keep their own title
columns this phase, see plan.md), and `crawl_job_domains` replaces
`crawl_jobs.domain_ids` JSON as the read path going forward (the JSON column
itself stays for one more phase). Guarded with an inspector per the
0011/0012 precedent, since `Database.__init__` runs `create_all()` before
this migration runs.
"""
import json
import logging
import sqlalchemy as sa

from alembic import op

log = logging.getLogger(__name__)

revision = '0013_add_lookups_and_job_domains'
down_revision = '0012_add_auth'
branch_labels = None
depends_on = None


def _seed_lookup(bind, lookup_table: str, source_table: str, code_col: str, title_col: str):
    """INSERT INTO <lookup_table> SELECT DISTINCT <code_col>, <title_col> FROM
    <source_table>, skipping codes already present. Uses a portable
    'WHERE NOT EXISTS' rather than ON CONFLICT since this runs on both
    SQLite (dev) and Postgres (prod)."""
    rows = bind.execute(sa.text(
        f"SELECT DISTINCT {code_col} AS code, {title_col} AS title FROM {source_table} "
        f"WHERE {code_col} IS NOT NULL"
    )).fetchall()
    for row in rows:
        exists = bind.execute(
            sa.text(f"SELECT 1 FROM {lookup_table} WHERE code = :code"),
            {"code": row.code},
        ).first()
        if not exists and row.title is not None:
            bind.execute(
                sa.text(f"INSERT INTO {lookup_table} (code, title) VALUES (:code, :title)"),
                {"code": row.code, "title": row.title},
            )


def _backfill_job_domains(bind):
    """Parse crawl_jobs.domain_ids JSON and insert (job_id, domain_id) pairs
    into crawl_job_domains, skipping dangling domain ids (logged via rowcount,
    not fatal) and tolerating '[]'/NULL."""
    rows = bind.execute(sa.text(
        "SELECT id, domain_ids FROM crawl_jobs WHERE domain_ids IS NOT NULL"
    )).fetchall()
    inserted = 0
    skipped = 0
    for row in rows:
        try:
            domain_ids = json.loads(row.domain_ids or "[]")
        except (TypeError, ValueError):
            continue
        for domain_id in domain_ids:
            domain_exists = bind.execute(
                sa.text("SELECT 1 FROM domains WHERE id = :id"), {"id": domain_id},
            ).first()
            if not domain_exists:
                skipped += 1
                continue
            already = bind.execute(
                sa.text("SELECT 1 FROM crawl_job_domains WHERE job_id = :job_id AND domain_id = :domain_id"),
                {"job_id": row.id, "domain_id": domain_id},
            ).first()
            if already:
                continue
            bind.execute(
                sa.text("INSERT INTO crawl_job_domains (job_id, domain_id) VALUES (:job_id, :domain_id)"),
                {"job_id": row.id, "domain_id": domain_id},
            )
            inserted += 1
    if skipped:
        log.info(f"crawl_job_domains backfill: skipped {skipped} dangling domain id(s)")


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'categories' not in tables:
        op.create_table(
            'categories',
            sa.Column('code', sa.String(), primary_key=True),
            sa.Column('title', sa.String(), nullable=False),
        )

    if 'org_types' not in tables:
        op.create_table(
            'org_types',
            sa.Column('code', sa.String(), primary_key=True),
            sa.Column('title', sa.String(), nullable=False),
        )

    if 'crawl_job_domains' not in tables:
        op.create_table(
            'crawl_job_domains',
            sa.Column('job_id', sa.Integer(), sa.ForeignKey('crawl_jobs.id', ondelete='CASCADE'),
                      primary_key=True),
            sa.Column('domain_id', sa.Integer(), sa.ForeignKey('domains.id'), primary_key=True),
        )

    if 'domains' in tables:
        _seed_lookup(bind, 'categories', 'domains', 'category_code', 'category_title')
        _seed_lookup(bind, 'org_types', 'domains', 'org_type', 'org_type_title')
    if 'crawl_snapshots' in tables:
        _seed_lookup(bind, 'categories', 'crawl_snapshots', 'category_code', 'category_title')
        _seed_lookup(bind, 'org_types', 'crawl_snapshots', 'org_type', 'org_type_title')

    if 'crawl_jobs' in tables and 'domain_ids' in {c['name'] for c in inspector.get_columns('crawl_jobs')}:
        _backfill_job_domains(bind)


def downgrade():
    op.drop_table('crawl_job_domains')
    op.drop_table('org_types')
    op.drop_table('categories')
