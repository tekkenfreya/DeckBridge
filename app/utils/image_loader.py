"""Lazy image loader with module-level cache to prevent Tkinter GC issues."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from tkinter import PhotoImage

logger = logging.getLogger(__name__)

# Module-level cache — images stored here survive Tkinter's garbage collector.
_cache: dict[str, PhotoImage] = {}


def _assets_root() -> Path:
    """Return the ``assets/icons/`` directory, honouring PyInstaller bundles."""
    if getattr(sys, "frozen", False):
        # Running as a PyInstaller one-file executable
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        # Normal Python run — walk up from this file to the project root
        base = Path(__file__).parent.parent.parent
    return base / "assets" / "icons"


def _cache_key(name: str, size: int) -> str:
    """Return a deterministic cache key for a given icon name and size."""
    return f"{name}@{size}"


def get(name: str, size: int = 16) -> PhotoImage | None:
    """Return the ``PhotoImage`` for *name* at *size*×*size* pixels.

    Loads from ``assets/icons/<name>.png`` on first call; subsequent calls
    return the cached object.  Returns ``None`` (and logs a warning) if the
    file is missing.
    """
    key = _cache_key(name, size)
    if key in _cache:
        return _cache[key]

    icon_path = _assets_root() / f"{name}.png"
    if not icon_path.is_file():
        logger.warning("Icon not found: %s", icon_path)
        return None

    try:
        img = PhotoImage(file=str(icon_path))
        # Scale to target size.  PhotoImage subsample/zoom work in integer steps;
        # we approximate by choosing the nearest integer scale factor.
        orig_w = img.width()
        orig_h = img.height()
        if orig_w > 0 and orig_h > 0:
            scale_w = max(1, round(orig_w / size))
            scale_h = max(1, round(orig_h / size))
            if scale_w > 1 or scale_h > 1:
                img = img.subsample(scale_w, scale_h)
        _cache[key] = img
        logger.debug("Loaded icon %r at %dpx", name, size)
        return img
    except Exception as exc:
        logger.warning("Failed to load icon %r: %s", name, exc)
        return None


def preload(names: list[str], size: int = 16) -> None:
    """Eagerly load a list of icons into the cache.

    Intended to be called on a widget's first ``<Map>`` event so that icons
    are ready before the user interacts with the UI.
    """
    for name in names:
        get(name, size)


def clear() -> None:
    """Remove all cached images.  Intended for use in tests only."""
    _cache.clear()
    logger.debug("ImageCache cleared")
