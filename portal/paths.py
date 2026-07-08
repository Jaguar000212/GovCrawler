"""
Application path resolution and first-run bootstrap.

Handles both dev mode (running from source) and PyInstaller frozen mode
(running as GovCrawler.exe).
"""
import os
import shutil
import sys
from pathlib import Path


def get_app_dir() -> Path:
    """The root directory (Writeable)."""
    if getattr(sys, 'frozen', False):
        # Compiled: Returns the folder where the .exe physically lives
        return Path(sys.executable).parent
    # Native: Steps up from /project_root/portal/paths.py -> /project_root
    return Path(__file__).resolve().parent.parent


def get_bundle_dir() -> Path:
    """The temporary PyInstaller extraction folder (Read-Only)."""
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    # Native: Steps up to project root
    return Path(__file__).resolve().parent.parent


APP_DIR = get_app_dir()
BUNDLE_DIR = get_bundle_dir()

# --- WRITEABLE PATHS (Next to the .exe) ---
PORTAL_LIVE_DIR = APP_DIR / "portal"
DATA_DIR = PORTAL_LIVE_DIR / "data"

LOG_FILE_PATH = DATA_DIR / "portal.log"
LIVE_CONFIG_PATH = PORTAL_LIVE_DIR / "config.yaml"
# The agent's own config lives in a separate file from the cloud's — they're
# no longer the same process, and (plan.md §19.1 Phase 9 Part 2) the agent
# only ever needs api.host/port from it; everything else the agent needs
# (which cloud server to talk to, its own agent_id) lives in agent/localdb.py
# instead. Kept side-by-side so a single machine can run both a dev cloud
# server AND an agent against it without one's config.yaml clobbering the
# other's.
AGENT_LIVE_CONFIG_PATH = PORTAL_LIVE_DIR / "agent_config.yaml"

# --- READ-ONLY PATHS (Inside the bundle) ---
BROWSER_PATH = APP_DIR / "playwright_browsers"
DEFAULT_CONFIG_PATH = BUNDLE_DIR / "portal" / "default_config.yaml"
AGENT_DEFAULT_CONFIG_PATH = BUNDLE_DIR / "portal" / "default_agent_config.yaml"
ICON_PATH = BUNDLE_DIR / "assets" / "favicon.ico"
ALEMBIC_INI_PATH = BUNDLE_DIR / "alembic.ini"
ALEMBIC_DIR = BUNDLE_DIR / "alembic"


def bootstrap(live_config_path: Path = LIVE_CONFIG_PATH, default_config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    """First-run setup: create data dirs, copy the default config to the live
    path if needed, and point Playwright at the bundled/local browser
    directory. Idempotent — safe to call from both `portal.config.load_config`
    and `load_agent_config`, in addition to whatever the entrypoint itself
    calls, regardless of call order."""
    if getattr(sys, 'frozen', False) and not live_config_path.exists():
        PORTAL_LIVE_DIR.mkdir(parents=True, exist_ok=True)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if default_config_path.exists():
            shutil.copy(default_config_path, live_config_path)
    else:
        # Safe to create in development mode too
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Force Playwright to use the bundled browser path BEFORE importing Playwright
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSER_PATH)
