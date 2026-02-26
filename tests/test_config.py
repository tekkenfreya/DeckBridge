"""Tests for app/config.py — ConfigManager."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import ConfigManager, DEFAULT_CONFIG


@pytest.fixture()
def tmp_config(tmp_path: Path) -> ConfigManager:
    """Return a ConfigManager backed by a temporary directory."""
    return ConfigManager(base_dir=tmp_path)


class TestDefaultConfig:
    def test_default_created_when_missing(self, tmp_path: Path) -> None:
        """Config file is created with defaults if it does not exist."""
        cm = ConfigManager(base_dir=tmp_path)
        assert (tmp_path / "config.json").exists()
        assert cm.get("theme") == "dark"

    def test_all_default_keys_present(self, tmp_config: ConfigManager) -> None:
        """Every key in DEFAULT_CONFIG is present after initialisation."""
        for key in DEFAULT_CONFIG:
            assert tmp_config.get(key) is not None or key in tmp_config.get_all()


class TestCorruptConfig:
    def test_corrupt_json_resets_to_defaults(self, tmp_path: Path) -> None:
        """A corrupt config.json triggers a silent reset, not a crash."""
        config_path = tmp_path / "config.json"
        config_path.write_text("{ this is not valid json !!!", encoding="utf-8")
        cm = ConfigManager(base_dir=tmp_path)
        assert cm.get("theme") == "dark"

    def test_non_dict_root_resets(self, tmp_path: Path) -> None:
        """A config.json whose root is not an object triggers a reset."""
        config_path = tmp_path / "config.json"
        config_path.write_text("[1, 2, 3]", encoding="utf-8")
        cm = ConfigManager(base_dir=tmp_path)
        assert cm.get("theme") == "dark"

    def test_reset_preserves_new_file(self, tmp_path: Path) -> None:
        """After a corrupt-reset, the config file is valid JSON."""
        config_path = tmp_path / "config.json"
        config_path.write_text("GARBAGE", encoding="utf-8")
        ConfigManager(base_dir=tmp_path)
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
        assert isinstance(loaded, dict)


class TestGetSet:
    def test_set_persists_to_disk(self, tmp_path: Path) -> None:
        """set() writes the updated value to disk."""
        cm = ConfigManager(base_dir=tmp_path)
        cm.set("theme", "light")
        # Re-load from disk
        cm2 = ConfigManager(base_dir=tmp_path)
        assert cm2.get("theme") == "light"

    def test_get_unknown_key_returns_default(self, tmp_config: ConfigManager) -> None:
        """get() returns the provided default for unknown keys."""
        assert tmp_config.get("nonexistent_key", "fallback") == "fallback"

    def test_get_unknown_key_returns_none_by_default(
        self, tmp_config: ConfigManager
    ) -> None:
        assert tmp_config.get("nonexistent_key") is None


class TestProfileRoundtrip:
    def test_save_and_retrieve_profile(self, tmp_config: ConfigManager) -> None:
        """A saved profile can be retrieved by name."""
        profile = {"name": "MyDeck", "host": "192.168.1.100", "username": "deck"}
        tmp_config.save_profile(profile)
        retrieved = tmp_config.get_profile("MyDeck")
        assert retrieved is not None
        assert retrieved["host"] == "192.168.1.100"

    def test_save_strips_password(self, tmp_config: ConfigManager) -> None:
        """Passwords are stripped from profiles before writing to disk."""
        profile = {
            "name": "SecureDeck",
            "host": "10.0.0.5",
            "username": "deck",
            "password": "hunter2",
        }
        tmp_config.save_profile(profile)
        retrieved = tmp_config.get_profile("SecureDeck")
        assert "password" not in retrieved  # type: ignore[operator]

    def test_upsert_replaces_existing_profile(self, tmp_config: ConfigManager) -> None:
        """Saving a profile with an existing name replaces it."""
        tmp_config.save_profile({"name": "MyDeck", "host": "192.168.1.1"})
        tmp_config.save_profile({"name": "MyDeck", "host": "10.0.0.1"})
        assert tmp_config.get_profile("MyDeck")["host"] == "10.0.0.1"  # type: ignore[index]
        assert len(tmp_config.get_profiles()) == 1

    def test_save_profile_without_name_raises(self, tmp_config: ConfigManager) -> None:
        """save_profile raises ValueError if the profile has no name."""
        with pytest.raises(ValueError, match="non-empty 'name'"):
            tmp_config.save_profile({"host": "192.168.1.1"})


class TestDeleteProfile:
    def test_delete_existing_profile(self, tmp_config: ConfigManager) -> None:
        """delete_profile removes the profile and returns True."""
        tmp_config.save_profile({"name": "ToDelete", "host": "192.168.1.1"})
        result = tmp_config.delete_profile("ToDelete")
        assert result is True
        assert tmp_config.get_profile("ToDelete") is None

    def test_delete_nonexistent_returns_false(self, tmp_config: ConfigManager) -> None:
        """delete_profile returns False when the profile does not exist."""
        result = tmp_config.delete_profile("GhostProfile")
        assert result is False

    def test_delete_persists_to_disk(self, tmp_path: Path) -> None:
        """After deletion, a re-loaded ConfigManager does not see the profile."""
        cm = ConfigManager(base_dir=tmp_path)
        cm.save_profile({"name": "ToDelete", "host": "192.168.1.1"})
        cm.delete_profile("ToDelete")
        cm2 = ConfigManager(base_dir=tmp_path)
        assert cm2.get_profile("ToDelete") is None


class TestSetupFlag:
    def test_setup_not_complete_initially(self, tmp_config: ConfigManager) -> None:
        """is_setup_complete returns False before mark_setup_complete is called."""
        assert tmp_config.is_setup_complete() is False

    def test_mark_setup_complete(self, tmp_config: ConfigManager) -> None:
        """After mark_setup_complete, is_setup_complete returns True."""
        tmp_config.mark_setup_complete()
        assert tmp_config.is_setup_complete() is True

    def test_reset_setup_clears_flag(self, tmp_config: ConfigManager) -> None:
        """reset_setup() removes the flag so wizard would show again."""
        tmp_config.mark_setup_complete()
        tmp_config.reset_setup()
        assert tmp_config.is_setup_complete() is False

    def test_setup_flag_survives_reload(self, tmp_path: Path) -> None:
        """The setup flag is persisted on disk and survives a re-instantiation."""
        cm = ConfigManager(base_dir=tmp_path)
        cm.mark_setup_complete()
        cm2 = ConfigManager(base_dir=tmp_path)
        assert cm2.is_setup_complete() is True
