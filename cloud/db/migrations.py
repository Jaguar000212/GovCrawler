import logging
from alembic.config import Config
from sqlalchemy import create_engine, inspect as sa_inspect

from alembic import command
from portal.paths import ALEMBIC_DIR, ALEMBIC_INI_PATH

log = logging.getLogger(__name__)


def run_migrations(db_uri: str) -> None:
    """Bring the database schema up to the latest Alembic revision.

    Installs that predate Alembic tracking already have a current schema —
    `Database.__init__` runs `create_all()` and `_ensure_columns()` first,
    which cover every additive change up to this point. So the first time
    an `alembic_version` table is missing, we stamp at `head` instead of
    replaying history (that would try to re-create tables/columns that
    already exist). From then on, `upgrade` is the real migration path for
    anything added after this point.
    """
    engine = create_engine(db_uri)
    try:
        has_version_table = sa_inspect(engine).has_table("alembic_version")
    finally:
        engine.dispose()

    cfg = Config(str(ALEMBIC_INI_PATH))
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", db_uri)

    if not has_version_table:
        command.stamp(cfg, "head")
        log.info("Alembic: no version table found — stamped existing schema at head")

    command.upgrade(cfg, "head")
    log.info("Alembic: database schema is up to date")
