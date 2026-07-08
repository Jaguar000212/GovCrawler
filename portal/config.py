"""load_config() — split out of portal/main.py so cloud-only entrypoints
(cloud/dispatch_service.py) can read config without transitively importing
portal.main's debug CLI, which imports agent.* (cmd_crawl) — that indirect
edge is exactly what the import-linter's "cloud must not import agent"
contract exists to catch (plan.md §19.1 Phase 9 Part 2, 2.7)."""

import logging
import os
import shutil
from pathlib import Path

import yaml

from .paths import DEFAULT_CONFIG_PATH, LIVE_CONFIG_PATH

log = logging.getLogger(__name__)


def load_config() -> dict:
    # Always read from the LIVE config next to the .exe
    target_config = LIVE_CONFIG_PATH if LIVE_CONFIG_PATH.exists() else (Path(__file__).parent / "config.yaml")

    if not target_config.exists():
        log.error(f"Config not found at: {target_config}")
        os.makedirs(target_config.parent, exist_ok=True)
        shutil.copy(DEFAULT_CONFIG_PATH, target_config)
    with open(target_config) as f:
        config = yaml.safe_load(f)

    # Container deployments (deploy/docker-compose.yml) point at Postgres via
    # env var rather than baking a second config.yaml into the image.
    # DATABASE_URL_APP (the least-privilege govcrawler_app role, see Alembic
    # 0020) takes precedence for runtime traffic when set; the `migrate`
    # service never sets it, so it always runs migrations with DATABASE_URL's
    # (superuser-ish) DDL rights. Local/dev/desktop installs without the
    # split role keep working on plain DATABASE_URL or the sqlite default.
    if os.environ.get("DATABASE_URL_APP"):
        config["database"]["uri"] = os.environ["DATABASE_URL_APP"]
    elif os.environ.get("DATABASE_URL"):
        config["database"]["uri"] = os.environ["DATABASE_URL"]
    if os.environ.get("DISPATCH_MODE"):
        config.setdefault("dispatch", {})["mode"] = os.environ["DISPATCH_MODE"]
    if os.environ.get("ADMIN_ORIGIN"):
        config.setdefault("auth", {})["admin_origin"] = os.environ["ADMIN_ORIGIN"]
    return config
