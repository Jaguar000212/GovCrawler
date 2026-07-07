"""
One-time SQLite -> Postgres data migration (Phase 1 — single source, no
cross-operator merge; that's a later phase per plan.md §14.2).

Copies every table 1:1 (remapping autoincrement PKs, since every table is
moving databases), seeds/backfills the two tables that changed shape this
phase (`categories`/`org_types`, `crawl_job_domains`), and leaves everything
else — including `test_campaigns`/`test_campaign_emails` and plaintext SMTP
passwords — untouched (those are Phase 2 work).

RUNBOOK ORDERING (do not skip):
    1. `docker compose -f deploy/docker-compose.yml up migrate` — creates the
       schema on Postgres via Alembic, nothing else.
    2. Run this script directly against that Postgres instance.
    3. Only then `docker compose -f deploy/docker-compose.yml up api` — the
       `api` service is the first thing that constructs `Database(config)`
       against the target, which runs `seed_rbac()`. Running that BEFORE
       this script would pre-insert `roles`/`permissions` rows with their
       own IDs, colliding with (or shadowing) the source's rows. This
       script defends against that ordering being violated anyway by
       matching `roles`/`permissions` by natural key (name/key), not by
       assuming an empty table — but don't rely on that as the primary
       safeguard.

Usage:
    python scripts/migrate_sqlite_to_pg.py <path/to/govcrawler.db> <postgres-url>
"""
import json
import logging
import sys
from pathlib import Path

import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from portal.db import Base, Database  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def _load_source(sqlite_path: str) -> Database:
    """Open the source through Database(), not raw sqlite3, so
    _ensure_columns()/run_migrations() have already brought it to the
    current schema before we read from it (a stale pre-upgrade file would
    otherwise be missing columns this script expects)."""
    config = {"database": {"uri": f"sqlite:///{sqlite_path}"}}
    return Database(config)


def copy_table(src_conn, dst_conn, table_name: str, id_maps: dict[str, dict[int, int]],
               fk_map: dict[str, str] | None = None, soft_fk: set[str] | None = None,
               has_own_id: bool = True) -> dict[int, int]:
    """Copy every row of `table_name` from src to dst, remapping FK columns
    named in `fk_map` (column -> table whose id_map to use) through
    `id_maps`. `soft_fk` columns are set to NULL (not skipped) if the
    referenced old id has no mapping — for nullable/non-enforced links
    (e.g. crawl_snapshots.source_domain_id). Non-soft FK columns with no
    mapping cause the row to be skipped (logged), since that FK is a real
    constraint on the target. Returns old_id -> new_id (empty dict if
    `has_own_id` is False — composite-PK junction tables)."""
    fk_map = fk_map or {}
    soft_fk = soft_fk or set()
    table = Base.metadata.tables[table_name]

    rows = src_conn.execute(sa.text(f"SELECT * FROM {table_name}")).mappings().all()
    id_map: dict[int, int] = {}
    skipped = 0

    for row in rows:
        values = dict(row)
        old_id = values.pop("id", None) if has_own_id else None

        remapped = True
        for col, ref_table in fk_map.items():
            old_val = values.get(col)
            if old_val is None:
                continue
            new_val = id_maps.get(ref_table, {}).get(old_val)
            if new_val is None:
                if col in soft_fk:
                    values[col] = None
                else:
                    remapped = False
                    break
            else:
                values[col] = new_val
        if not remapped:
            skipped += 1
            continue

        if has_own_id:
            result = dst_conn.execute(table.insert().returning(table.c.id), values)
            new_id = result.scalar_one()
            if old_id is not None:
                id_map[old_id] = new_id
        else:
            dst_conn.execute(table.insert(), values)

    if skipped:
        log.warning(f"{table_name}: skipped {skipped} row(s) with an unresolvable hard FK")
    log.info(f"{table_name}: copied {len(rows)} row(s)")
    return id_map


def copy_users(src_conn, dst_conn) -> dict[int, int]:
    """users.created_by is self-referential — insert with created_by=NULL
    first, then a second pass to fill it in once every user has a new id."""
    table = Base.metadata.tables["users"]
    rows = src_conn.execute(sa.text("SELECT * FROM users")).mappings().all()
    id_map: dict[int, int] = {}
    pending_created_by: list[tuple[int, int]] = []  # (new_id, old_created_by)

    for row in rows:
        values = dict(row)
        old_id = values.pop("id")
        old_created_by = values.pop("created_by", None)
        values["created_by"] = None
        result = dst_conn.execute(table.insert().returning(table.c.id), values)
        new_id = result.scalar_one()
        id_map[old_id] = new_id
        if old_created_by is not None:
            pending_created_by.append((new_id, old_created_by))

    for new_id, old_created_by in pending_created_by:
        new_created_by = id_map.get(old_created_by)
        if new_created_by is not None:
            dst_conn.execute(
                table.update().where(table.c.id == new_id).values(created_by=new_created_by)
            )

    log.info(f"users: copied {len(rows)} row(s)")
    return id_map


def copy_natural_key_table(src_conn, dst_conn, table_name: str, key_col: str) -> None:
    """For roles/permissions: match by natural key rather than assume an
    empty table, in case seed_rbac() has already run against the target
    (see the runbook-ordering warning in this file's docstring)."""
    table = Base.metadata.tables[table_name]
    rows = src_conn.execute(sa.text(f"SELECT * FROM {table_name}")).mappings().all()
    existing_keys = {
        r[0] for r in dst_conn.execute(sa.text(f"SELECT {key_col} FROM {table_name}")).fetchall()
    }
    inserted = 0
    for row in rows:
        values = dict(row)
        if values.get(key_col) in existing_keys:
            continue
        dst_conn.execute(table.insert(), values)
        inserted += 1
    log.info(f"{table_name}: inserted {inserted} new row(s) (natural-key match)")


def copy_roles(src_conn, dst_conn) -> dict[int, int]:
    """roles.id is a real autoincrement FK target (users.role_id,
    role_permissions.role_id) — match by name, but still build an
    old_id->new_id map either way."""
    table = Base.metadata.tables["roles"]
    rows = src_conn.execute(sa.text("SELECT * FROM roles")).mappings().all()
    existing = {
        r.name: r.id for r in dst_conn.execute(sa.text("SELECT id, name FROM roles")).fetchall()
    }
    id_map: dict[int, int] = {}
    for row in rows:
        values = dict(row)
        old_id = values.pop("id")
        if values["name"] in existing:
            id_map[old_id] = existing[values["name"]]
            continue
        result = dst_conn.execute(table.insert().returning(table.c.id), values)
        id_map[old_id] = result.scalar_one()
    log.info(f"roles: mapped {len(id_map)} row(s)")
    return id_map


def copy_role_permissions(src_conn, dst_conn, role_id_map: dict[int, int]) -> None:
    table = Base.metadata.tables["role_permissions"]
    rows = src_conn.execute(sa.text("SELECT * FROM role_permissions")).mappings().all()
    for row in rows:
        values = dict(row)
        values.pop("id", None)
        new_role_id = role_id_map.get(values["role_id"])
        if new_role_id is None:
            continue
        values["role_id"] = new_role_id
        exists = dst_conn.execute(
            sa.text("SELECT 1 FROM role_permissions WHERE role_id = :r AND permission_key = :p"),
            {"r": new_role_id, "p": values["permission_key"]},
        ).first()
        if not exists:
            dst_conn.execute(table.insert(), values)
    log.info(f"role_permissions: processed {len(rows)} row(s)")


def seed_lookups_from_domains(src_conn, dst_conn) -> None:
    """categories/org_types: distinct (code, title) pairs from the source
    domains + crawl_snapshots tables (a code seen only in a snapshot still
    needs a row), skip codes already present on the target."""
    for lookup_table, source_table, code_col, title_col in [
        ("categories", "domains", "category_code", "category_title"),
        ("org_types", "domains", "org_type", "org_type_title"),
        ("categories", "crawl_snapshots", "category_code", "category_title"),
        ("org_types", "crawl_snapshots", "org_type", "org_type_title"),
    ]:
        table = Base.metadata.tables[lookup_table]
        rows = src_conn.execute(sa.text(
            f"SELECT DISTINCT {code_col} AS code, {title_col} AS title FROM {source_table} "
            f"WHERE {code_col} IS NOT NULL"
        )).mappings().all()
        existing = {
            r[0] for r in dst_conn.execute(sa.text(f"SELECT code FROM {lookup_table}")).fetchall()
        }
        for row in rows:
            if row["code"] in existing or row["title"] is None:
                continue
            dst_conn.execute(table.insert(), {"code": row["code"], "title": row["title"]})
            existing.add(row["code"])
    log.info("categories/org_types: seeded from source domains + crawl_snapshots")


def backfill_job_domains(src_conn, dst_conn, domain_id_map: dict[int, int],
                         job_id_map: dict[int, int]) -> None:
    """Copy crawl_job_domains rows (already populated in the source by
    Alembic revision 0013), remapping both FKs. Falls back to parsing
    crawl_jobs.domain_ids JSON if the source predates 0013 for some reason."""
    table = Base.metadata.tables["crawl_job_domains"]
    rows = src_conn.execute(sa.text("SELECT job_id, domain_id FROM crawl_job_domains")).mappings().all()
    if not rows:
        job_rows = src_conn.execute(
            sa.text("SELECT id, domain_ids FROM crawl_jobs WHERE domain_ids IS NOT NULL")
        ).mappings().all()
        rows = []
        for jr in job_rows:
            try:
                for d in json.loads(jr["domain_ids"] or "[]"):
                    rows.append({"job_id": jr["id"], "domain_id": d})
            except (TypeError, ValueError):
                continue

    inserted, skipped = 0, 0
    for row in rows:
        new_job_id = job_id_map.get(row["job_id"])
        new_domain_id = domain_id_map.get(row["domain_id"])
        if new_job_id is None or new_domain_id is None:
            skipped += 1
            continue
        dst_conn.execute(table.insert(), {"job_id": new_job_id, "domain_id": new_domain_id})
        inserted += 1
    log.info(f"crawl_job_domains: inserted {inserted} row(s), skipped {skipped} dangling")


def verify(dst_conn, expected_counts: dict[str, int]) -> None:
    """Row counts vs source, plus a few representative dangling-FK checks
    (not exhaustive — the highest-value relationships only)."""
    for table_name, expected in expected_counts.items():
        actual = dst_conn.execute(sa.text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one()
        status = "OK" if actual == expected else "MISMATCH"
        log.info(f"verify {table_name}: source={expected} target={actual} [{status}]")

    checks = [
        ("leads", "job_id", "crawl_jobs", "id"),
        ("campaign_emails", "campaign_id", "campaigns", "id"),
        ("campaign_emails", "lead_id", "leads", "id"),
        ("users", "role_id", "roles", "id"),
    ]
    for child, fk_col, parent, parent_col in checks:
        dangling = dst_conn.execute(sa.text(
            f"SELECT COUNT(*) FROM {child} c LEFT JOIN {parent} p "
            f"ON c.{fk_col} = p.{parent_col} WHERE c.{fk_col} IS NOT NULL AND p.{parent_col} IS NULL"
        )).scalar_one()
        log.info(f"verify {child}.{fk_col} -> {parent}.{parent_col}: {dangling} dangling row(s)")


def migrate(sqlite_path: str, pg_url: str) -> None:
    src_db = _load_source(sqlite_path)
    dst_engine = sa.create_engine(pg_url)

    with src_db.engine.connect() as src_conn, dst_engine.begin() as dst_conn:
        expected_counts: dict[str, int] = {}

        def count(table_name: str) -> int:
            n = src_conn.execute(sa.text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one()
            expected_counts[table_name] = n
            return n

        seed_lookups_from_domains(src_conn, dst_conn)

        count("roles")
        role_id_map = copy_roles(src_conn, dst_conn)
        count("permissions")
        copy_natural_key_table(src_conn, dst_conn, "permissions", "key")
        count("role_permissions")
        copy_role_permissions(src_conn, dst_conn, role_id_map)

        count("users")
        user_id_map = copy_users(src_conn, dst_conn)
        # users.role_id needs remapping too — done as a follow-up pass since
        # copy_users() doesn't know about role_id_map.
        users_table = Base.metadata.tables["users"]
        for old_id, new_id in user_id_map.items():
            old_role_id = src_conn.execute(
                sa.text("SELECT role_id FROM users WHERE id = :id"), {"id": old_id}
            ).scalar_one_or_none()
            if old_role_id is not None:
                new_role_id = role_id_map.get(old_role_id)
                if new_role_id is not None:
                    dst_conn.execute(
                        users_table.update().where(users_table.c.id == new_id).values(role_id=new_role_id)
                    )

        count("user_permissions")
        copy_table(src_conn, dst_conn, "user_permissions", {"users": user_id_map},
                  fk_map={"user_id": "users"})
        count("user_sessions")
        copy_table(src_conn, dst_conn, "user_sessions", {"users": user_id_map},
                  fk_map={"user_id": "users"})
        count("audit_log")
        copy_table(src_conn, dst_conn, "audit_log", {"users": user_id_map},
                  fk_map={"user_id": "users"}, soft_fk={"user_id"})

        count("domains")
        domain_id_map = copy_table(src_conn, dst_conn, "domains", {})

        count("crawl_jobs")
        job_id_map = copy_table(src_conn, dst_conn, "crawl_jobs", {})

        count("crawl_job_domains")
        backfill_job_domains(src_conn, dst_conn, domain_id_map, job_id_map)

        count("job_custom_urls")
        copy_table(src_conn, dst_conn, "job_custom_urls", {"crawl_jobs": job_id_map},
                  fk_map={"job_id": "crawl_jobs"})

        count("crawl_snapshots")
        snapshot_id_map = copy_table(
            src_conn, dst_conn, "crawl_snapshots", {"crawl_jobs": job_id_map, "domains": domain_id_map},
            fk_map={"job_id": "crawl_jobs", "source_domain_id": "domains"}, soft_fk={"source_domain_id"},
        )

        count("visited_urls")
        copy_table(src_conn, dst_conn, "visited_urls", {"crawl_jobs": job_id_map},
                  fk_map={"job_id": "crawl_jobs"})

        count("leads")
        lead_id_map = copy_table(
            src_conn, dst_conn, "leads",
            {"crawl_jobs": job_id_map, "domains": domain_id_map, "crawl_snapshots": snapshot_id_map},
            fk_map={"job_id": "crawl_jobs", "domain_id": "domains", "snapshot_id": "crawl_snapshots"},
            soft_fk={"domain_id", "snapshot_id"},
        )

        count("email_templates")
        template_id_map = copy_table(src_conn, dst_conn, "email_templates", {})

        count("smtp_credentials")
        credential_id_map = copy_table(src_conn, dst_conn, "smtp_credentials", {})

        count("campaigns")
        campaign_id_map = copy_table(
            src_conn, dst_conn, "campaigns", {"email_templates": template_id_map},
            fk_map={"template_id": "email_templates"}, soft_fk={"template_id"},
        )
        count("test_campaigns")
        test_campaign_id_map = copy_table(
            src_conn, dst_conn, "test_campaigns",
            {"email_templates": template_id_map, "smtp_credentials": credential_id_map},
            fk_map={"template_id": "email_templates", "test_credential_id": "smtp_credentials"},
            soft_fk={"template_id", "test_credential_id"},
        )

        count("campaign_credentials")
        copy_table(
            src_conn, dst_conn, "campaign_credentials",
            {"campaigns": campaign_id_map, "smtp_credentials": credential_id_map},
            fk_map={"campaign_id": "campaigns", "credential_id": "smtp_credentials"},
        )

        count("campaign_emails")
        copy_table(
            src_conn, dst_conn, "campaign_emails",
            {"campaigns": campaign_id_map, "leads": lead_id_map, "smtp_credentials": credential_id_map},
            fk_map={"campaign_id": "campaigns", "lead_id": "leads", "credential_id": "smtp_credentials"},
            soft_fk={"credential_id"},
        )
        count("test_campaign_emails")
        copy_table(
            src_conn, dst_conn, "test_campaign_emails",
            {"test_campaigns": test_campaign_id_map, "smtp_credentials": credential_id_map},
            fk_map={"test_campaign_id": "test_campaigns", "credential_id": "smtp_credentials"},
            soft_fk={"credential_id"},
        )

        count("blacklist")
        copy_table(src_conn, dst_conn, "blacklist", {})

        verify(dst_conn, expected_counts)

    src_db.close()
    log.info("Migration complete.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python scripts/migrate_sqlite_to_pg.py <path/to/govcrawler.db> <postgres-url>")
        sys.exit(1)
    migrate(sys.argv[1], sys.argv[2])
