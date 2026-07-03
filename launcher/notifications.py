"""
Cross-platform toast notifications for the GovCrawler control panel.
"""

import logging
import tempfile
from pathlib import Path

from notifypy import Notify

log = logging.getLogger(__name__)

_png_icon_cache: Path | None = None


def _icon_as_png(icon_path: Path) -> str | None:
    """notifypy needs a .png for reliable cross-platform rendering; the only
    bundled icon is a .ico, so convert it once and cache the result."""
    global _png_icon_cache
    if _png_icon_cache is not None:
        return str(_png_icon_cache)
    try:
        from PIL import Image

        tmp = Path(tempfile.gettempdir()) / "govcrawler_notify_icon.png"
        Image.open(icon_path).save(tmp, format="PNG")
        _png_icon_cache = tmp
        return str(_png_icon_cache)
    except Exception as e:
        log.warning(f"Could not prepare notification icon: {e}")
        return None


def notify(title: str, msg: str, icon_path: Path) -> None:
    try:
        notification = Notify()
        notification.application_name = "GovCrawler"
        notification.title = title
        notification.message = msg
        if icon_path.exists():
            icon = _icon_as_png(icon_path)
            if icon:
                notification.icon = icon
        notification.send()
    except Exception as e:
        log.warning(f"Notification failed: {e}")
