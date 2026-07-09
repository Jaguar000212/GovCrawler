"""Agent-owned shared state for the job-lifecycle routes (agent/api.py):
the shared Playwright browser, the app config dict, and the in-process
active-crawl-task registry. These are agent/crawler concerns, not cloud
ones — they only used to live in cloud.api.deps because both tiers share
one process today. cloud/api/server.py's lifespan sets the browser/config
here once at startup (the same already-flagged cloud -> agent exception as
mounting agent.api.router, not a new one). Zero cloud.* imports.
"""

import asyncio

_active_tasks: dict[int, asyncio.Task] = {}
_browser = None
_config: dict | None = None


def get_active_tasks() -> dict[int, asyncio.Task]:
    return _active_tasks


def set_browser(browser) -> None:
    global _browser
    _browser = browser


def get_browser():
    return _browser


def set_config(config: dict) -> None:
    global _config
    _config = config


def get_config() -> dict:
    return _config
