"""DeckBridge application — main App class and routing logic."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk

from app.config import ConfigManager

logger = logging.getLogger(__name__)


class App(ttk.Frame):
    """Root application frame; routes between the setup wizard and main window."""

    def __init__(self, master: tk.Tk) -> None:
        """Initialise the App frame and kick off routing."""
        super().__init__(master)
        self.master = master
        self.pack(fill=tk.BOTH, expand=True)
        self._config = ConfigManager()
        self._route()

    def _route(self) -> None:
        """Show the wizard if setup is incomplete or no profiles exist; else main window."""
        if not self._config.is_setup_complete():
            logger.info("First run — launching setup wizard")
            self._launch_wizard()
            return

        profiles = self._config.get_profiles()
        if not profiles:
            # setup_complete exists but no profiles — previous run didn't save a profile.
            # Reset so the wizard runs again and saves one.
            logger.warning("Setup complete but no profiles found — resetting to wizard")
            self._config.reset_setup()
            self._launch_wizard()
            return

        logger.info("Setup complete with %d profile(s) — launching main window", len(profiles))
        self._launch_main_window()

    def _launch_wizard(self) -> None:
        """Create and display the setup wizard."""
        from app.ui.wizard import Wizard

        self._wizard = Wizard(self, on_complete=self._on_wizard_complete)
        self._wizard.pack(fill=tk.BOTH, expand=True)

    def _on_wizard_complete(self, connection=None) -> None:
        """Called when the wizard's CompleteStep fires its callback."""
        logger.info("Wizard completed — transitioning to main window")
        for child in self.winfo_children():
            child.destroy()
        self._launch_main_window(connection=connection)

    def _launch_main_window(self, connection=None) -> None:
        """Create and display the main dual-pane file browser.

        If *connection* is provided (wizard flow), wires it in immediately.
        Otherwise triggers an auto-connect from the most recent saved profile.
        """
        from app.ui.main_window import MainWindow

        self._main_window = MainWindow(self, config=self._config)
        self._main_window.pack(fill=tk.BOTH, expand=True)

        if connection is not None:
            self._main_window.set_connection(connection)
            logger.info("Live connection wired into main window from wizard")
        else:
            # Auto-connect on every launch using the saved profile
            self.after(200, self._main_window.auto_connect)

        logger.info("Main window launched")
