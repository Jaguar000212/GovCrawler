"""Dedupe leads by email globally + make email uniqueness job-independent

Revision ID: 0028_dedupe_lead_email_global
Revises: 0027_cascade_job_domains_fk

Lead had UniqueConstraint(job_id, email), but save_lead()/bulk_upsert_manual_leads()
query-and-dedupe by email ALONE (leads are a global shared pool, not per-job). Two
jobs capturing the same email at nearly the same time could both pass the "no
existing" check before either committed — the composite constraint didn't stop the
second insert (different job_id), silently producing two rows for one email.

Before adding a plain (email) unique constraint, any existing duplicates must be
merged or the constraint creation itself would fail. For each email with >1 row:
picks a canonical row (highest confidence_band, then lowest id), enrich-merges the
others' non-blank fields into it (same fill-if-null rule as save_lead's
enrich-on-conflict), re-points campaign_emails.lead_id and lead_occurrences at the
canonical row (dropping an occurrence that would collide with one the canonical
already has for that job), then deletes the duplicate row. No email or occurrence
history is lost — only the redundant row.
"""

import logging
import sqlalchemy as sa

from alembic import op

log = logging.getLogger(__name__)

revision = "0028_dedupe_lead_email_global"
down_revision = "0027_cascade_job_domains_fk"
branch_labels = None
depends_on = None

_ENRICHABLE_FIELDS = (
    "person_name",
    "designation",
    "department",
    "source_title",
    "context_snippet",
    "phone",
    "entity_kind",
)
_BAND_RANK = {"LOW": 0, "HIGH": 1}


def _pick_canonical(rows):
    return max(rows, key=lambda r: (_BAND_RANK.get(r.confidence_band, -1), -r.id))


def _dedupe_leads(bind) -> int:
    dupe_emails = bind.execute(sa.text("SELECT email FROM leads GROUP BY email HAVING COUNT(*) > 1")).fetchall()
    if not dupe_emails:
        return 0

    merged = 0
    for (email,) in dupe_emails:
        rows = bind.execute(
            sa.text(
                "SELECT id, confidence_band, person_name, designation, department, "
                "source_title, context_snippet, phone, entity_kind FROM leads WHERE email = :email"
            ),
            {"email": email},
        ).fetchall()
        canonical = _pick_canonical(rows)

        for row in rows:
            if row.id == canonical.id:
                continue

            for field in _ENRICHABLE_FIELDS:
                dup_value = getattr(row, field)
                if dup_value:
                    bind.execute(
                        sa.text(
                            f"UPDATE leads SET {field} = :value WHERE id = :id "
                            f"AND ({field} IS NULL OR {field} = '')"
                        ),
                        {"value": dup_value, "id": canonical.id},
                    )
            if _BAND_RANK.get(row.confidence_band, -1) > _BAND_RANK.get(canonical.confidence_band, -1):
                bind.execute(
                    sa.text("UPDATE leads SET confidence_band = :band WHERE id = :id"),
                    {"band": row.confidence_band, "id": canonical.id},
                )

            bind.execute(
                sa.text("UPDATE campaign_emails SET lead_id = :canonical WHERE lead_id = :dup"),
                {"canonical": canonical.id, "dup": row.id},
            )

            occ_rows = bind.execute(
                sa.text("SELECT id, job_id FROM lead_occurrences WHERE lead_id = :dup"), {"dup": row.id}
            ).fetchall()
            for occ in occ_rows:
                exists = bind.execute(
                    sa.text("SELECT 1 FROM lead_occurrences WHERE lead_id = :canonical AND job_id = :job_id"),
                    {"canonical": canonical.id, "job_id": occ.job_id},
                ).first()
                if exists:
                    bind.execute(sa.text("DELETE FROM lead_occurrences WHERE id = :id"), {"id": occ.id})
                else:
                    bind.execute(
                        sa.text("UPDATE lead_occurrences SET lead_id = :canonical WHERE id = :id"),
                        {"canonical": canonical.id, "id": occ.id},
                    )

            bind.execute(sa.text("DELETE FROM leads WHERE id = :id"), {"id": row.id})
            merged += 1
    return merged


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "leads" not in inspector.get_table_names():
        return

    merged = _dedupe_leads(bind)
    if merged:
        log.info(f"leads dedup: merged {merged} duplicate row(s) sharing an email across jobs")

    existing = {c["name"] for c in inspector.get_unique_constraints("leads")}
    with op.batch_alter_table("leads") as batch_op:
        if "uq_lead_job_email" in existing:
            batch_op.drop_constraint("uq_lead_job_email", type_="unique")
        if "uq_lead_email" not in existing:
            batch_op.create_unique_constraint("uq_lead_email", ["email"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "leads" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_unique_constraints("leads")}
    with op.batch_alter_table("leads") as batch_op:
        if "uq_lead_email" in existing:
            batch_op.drop_constraint("uq_lead_email", type_="unique")
        if "uq_lead_job_email" not in existing:
            batch_op.create_unique_constraint("uq_lead_job_email", ["job_id", "email"])
