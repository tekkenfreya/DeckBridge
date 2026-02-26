"""Configuration and profile management for DeckBridge.

All settings are stored as JSON files under ``~/.deckbridge/``.
Passwords are never written to disk — they are delegated to ``keyring``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    "theme": "dark",
    "show_hidden_files": False,
    "transfer_chunk_size": 32768,
    "ssh_timeout": 15,
    "reconnect_retries": 3,
    "reconnect_base_delay": 2,
    "keepalive_interval": 30,
    "local_start_path": str(Path.home()),
    "remote_start_path": "/home/deck",
}

# ---------------------------------------------------------------------------
# ConfigManager
# ---------------------------------------------------------------------------


class ConfigManager:
    """Manages application settings and connection profiles.

    Writes files atomically (write-to-temp, then rename) to prevent
    corruption on unexpected exit.  A corrupt config triggers a warning and
    a safe reset — it never crashes the application.
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        """Initialise, creating ``~/.deckbridge/`` if necessary."""
        self._base = base_dir or Path.home() / ".deckbridge"
        self._config_path = self._base / "config.json"
        self._profiles_path = self._base / "profiles.json"
        self._setup_flag = self._base / "setup_complete"

        self._base.mkdir(parents=True, exist_ok=True)
        self._config: dict[str, Any] = self._load_config()
        self._profiles: list[dict[str, Any]] = self._load_profiles()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _atomic_write(self, path: Path, data: Any) -> None:
        """Serialise *data* as JSON and write atomically to *path*."""
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            logger.error("Failed to write %s: %s", path, exc)
            raise

    def _load_config(self) -> dict[str, Any]:
        """Load ``config.json``, resetting to defaults on corruption."""
        if not self._config_path.exists():
            logger.debug("No config file — creating defaults")
            config = dict(DEFAULT_CONFIG)
            self._atomic_write(self._config_path, config)
            return config

        try:
            raw = self._config_path.read_text(encoding="utf-8")
            loaded = json.loads(raw)
            if not isinstance(loaded, dict):
                raise ValueError("Config root must be a JSON object")
            # Merge with defaults so new keys are always present
            merged = dict(DEFAULT_CONFIG)
            merged.update(loaded)
            return merged
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            logger.warning(
                "Corrupt config.json (%s) — resetting to defaults", exc
            )
            config = dict(DEFAULT_CONFIG)
            self._atomic_write(self._config_path, config)
            return config

    def _load_profiles(self) -> list[dict[str, Any]]:
        """Load ``profiles.json``, returning an empty list on corruption."""
        if not self._profiles_path.exists():
            return []
        try:
            raw = self._profiles_path.read_text(encoding="utf-8")
            loaded = json.loads(raw)
            if not isinstance(loaded, list):
                raise ValueError("Profiles root must be a JSON array")
            return loaded
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            logger.warning(
                "Corrupt profiles.json (%s) — resetting to empty list", exc
            )
            self._atomic_write(self._profiles_path, [])
            return []

    # ------------------------------------------------------------------
    # Config access
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Return the value for *key*, or *default* if missing."""
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set *key* to *value* and persist the config file."""
        self._config[key] = value
        self._atomic_write(self._config_path, self._config)
        logger.debug("Config updated: %s = %r", key, value)

    def get_all(self) -> dict[str, Any]:
        """Return a shallow copy of the full config dict."""
        return dict(self._config)

    # ------------------------------------------------------------------
    # Setup flag
    # ------------------------------------------------------------------

    def is_setup_complete(self) -> bool:
        """Return True if the first-time setup wizard has been completed."""
        return self._setup_flag.exists()

    def mark_setup_complete(self) -> None:
        """Create the ``setup_complete`` flag file."""
        try:
            self._setup_flag.touch()
            logger.info("Setup marked as complete")
        except OSError as exc:
            logger.error("Could not write setup_complete flag: %s", exc)
            raise

    def reset_setup(self) -> None:
        """Remove the ``setup_complete`` flag (for testing / re-run wizard)."""
        if self._setup_flag.exists():
            self._setup_flag.unlink()
            logger.info("Setup flag removed")

    # ------------------------------------------------------------------
    # Profile management
    # ------------------------------------------------------------------

    def get_profiles(self) -> list[dict[str, Any]]:
        """Return a copy of all saved connection profiles."""
        return list(self._profiles)

    def save_profile(self, profile: dict[str, Any]) -> None:
        """Upsert a profile by its ``name`` field.

        If a profile with the same ``name`` already exists it is replaced;
        otherwise the new profile is appended.  Passwords must NOT be in
        *profile* — store them via ``keyring`` externally.
        """
        name = profile.get("name")
        if not name:
            raise ValueError("Profile must have a non-empty 'name' field")

        # Strip any accidental password keys
        profile = {k: v for k, v in profile.items() if k != "password"}

        for i, existing in enumerate(self._profiles):
            if existing.get("name") == name:
                self._profiles[i] = profile
                break
        else:
            self._profiles.append(profile)

        self._atomic_write(self._profiles_path, self._profiles)
        logger.info("Profile saved: %s", name)

    def delete_profile(self, name: str) -> bool:
        """Delete the profile identified by *name*.

        Returns ``True`` if a profile was deleted, ``False`` if not found.
        """
        original_len = len(self._profiles)
        self._profiles = [p for p in self._profiles if p.get("name") != name]
        if len(self._profiles) < original_len:
            self._atomic_write(self._profiles_path, self._profiles)
            logger.info("Profile deleted: %s", name)
            return True
        logger.warning("delete_profile: profile not found: %s", name)
        return False

    def get_profile(self, name: str) -> dict[str, Any] | None:
        """Return the profile dict for *name*, or ``None`` if not found."""
        for profile in self._profiles:
            if profile.get("name") == name:
                return dict(profile)
        return None
