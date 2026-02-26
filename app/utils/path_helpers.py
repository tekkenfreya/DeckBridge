"""Cross-platform path normalisation and validation utilities."""

from __future__ import annotations

import logging
import os
import posixpath
import sys
from pathlib import Path, PurePosixPath

logger = logging.getLogger(__name__)

# Sentinel path used on Windows to represent the virtual "all drives" root.
DRIVES_ROOT = "__drives__"


def posix_join(*parts: str) -> str:
    """Join path parts using POSIX (forward-slash) rules.

    Suitable for constructing remote Steam Deck paths regardless of the
    local OS.
    """
    return posixpath.join(*parts)


def human_readable_size(size_bytes: int | float) -> str:
    """Convert a byte count to a human-readable string (e.g. "4.2 MB").

    Uses 1024-based units (KiB/MiB/GiB) but labels them KB/MB/GB for
    familiarity with everyday usage.
    """
    if size_bytes < 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024.0:
            if unit == "B":
                return f"{int(size_bytes)} B"
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def validate_remote_path(path: str) -> bool:
    """Return True if *path* is safe for SFTP operations.

    Rejects paths that contain null bytes or path-traversal sequences (``..``).
    """
    if "\x00" in path:
        logger.warning("Remote path rejected — contains null byte: %r", path)
        return False
    # Resolve to a normalised POSIX path and check for traversal
    try:
        resolved = str(PurePosixPath(path))
    except Exception:
        logger.warning("Remote path rejected — could not parse: %r", path)
        return False
    parts = resolved.split("/")
    if ".." in parts:
        logger.warning("Remote path rejected — contains '..': %r", path)
        return False
    return True


def normalize_local_path(path: str | os.PathLike[str]) -> Path:
    """Resolve *path* to an absolute ``pathlib.Path`` on the local filesystem."""
    return Path(path).expanduser().resolve()


def get_path_segments(path: str) -> list[tuple[str, str]]:
    """Split *path* into (label, full_path) segments for breadcrumb rendering.

    On Windows, a leading "Drives" segment is prepended so the user can
    navigate back to the virtual drive-list root.

    Example (POSIX)::

        >>> get_path_segments("/home/deck/.local/share")
        [("/", "/"), ("home", "/home"), ("deck", "/home/deck"),
         (".local", "/home/deck/.local"), ("share", "/home/deck/.local/share")]
    """
    if not path:
        return []

    # Virtual drives-root sentinel (Windows only)
    if path == DRIVES_ROOT:
        return [("Drives", DRIVES_ROOT)]

    # Detect whether this is a POSIX remote path or a local Windows path
    is_posix = path.startswith("/")

    if is_posix:
        parts = [p for p in path.split("/") if p]
        segments: list[tuple[str, str]] = [("/", "/")]
        cumulative = ""
        for part in parts:
            cumulative = f"{cumulative}/{part}"
            segments.append((part, cumulative))
        return segments
    else:
        # Windows local path — prepend a virtual "Drives" root so the user
        # can navigate above the drive letter in the breadcrumb bar.
        p = Path(path)
        parts_list = list(p.parts)
        if not parts_list:
            return []
        segments: list[tuple[str, str]] = []
        if sys.platform == "win32":
            segments.append(("Drives", DRIVES_ROOT))
        cumulative_path = Path(parts_list[0])
        segments.append((parts_list[0], str(cumulative_path)))
        for part in parts_list[1:]:
            cumulative_path = cumulative_path / part
            segments.append((part, str(cumulative_path)))
        return segments
