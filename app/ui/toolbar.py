"""Quick-navigate toolbar for DeckBridge.

Renders a scrollable horizontal row of shortcut buttons to common
Steam Deck paths.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk
from typing import Callable

from app.ui.components import Tooltip

logger = logging.getLogger(__name__)

_DARK_BG = "#1b2838"
_DARK_FG = "#c7d5e0"
_DARK_ACCENT = "#1a9fff"
_DARK_BORDER = "#374e6a"

# Verified Steam Deck paths (SteamOS 3.x)
QUICK_NAV_SHORTCUTS: list[tuple[str, str]] = [
    ("Desktop", "~/Desktop"),
    ("Downloads", "~/Downloads"),
    ("Steam", "~/.local/share/Steam"),
    ("Flatpak", "~/.var/app"),
    ("SD Card", "/run/media/"),
    ("Save Data", "~/.local/share/Steam/steamapps/compatdata"),
]


class QuickNavToolbar(ttk.Frame):
    """A scrollable horizontal toolbar with Steam Deck quick-navigate buttons.

    Each button navigates the remote pane to a predefined path.  If the
    path does not exist on the Deck, the remote pane's error handling will
    show a non-blocking warning.
    """

    def __init__(
        self,
        master: tk.Widget,
        on_navigate: Callable[[str], None],
        **kwargs,
    ) -> None:
        """Create the toolbar wired to *on_navigate*."""
        super().__init__(master, **kwargs)
        self._on_navigate = on_navigate
        self._build()

    def _build(self) -> None:
        """Construct the scrollable canvas with shortcut buttons."""
        ttk.Label(
            self,
            text="Steam Deck:",
            font=("TkDefaultFont", 10),
            foreground=_DARK_FG,
            padding=(6, 0),
        ).pack(side=tk.LEFT)

        self._canvas = tk.Canvas(
            self,
            background=_DARK_BG,
            height=32,
            highlightthickness=0,
        )
        self._scrollbar = ttk.Scrollbar(
            self, orient=tk.HORIZONTAL, command=self._canvas.xview
        )
        self._canvas.configure(xscrollcommand=self._scrollbar.set)

        # We embed a frame inside the canvas for the buttons
        self._btn_frame = ttk.Frame(self._canvas)
        self._btn_frame_id = self._canvas.create_window(
            0, 0, anchor=tk.NW, window=self._btn_frame
        )

        self._canvas.pack(side=tk.TOP, fill=tk.X, expand=True)
        self._scrollbar.pack(side=tk.BOTTOM, fill=tk.X)

        self._btn_frame.bind("<Configure>", self._on_frame_configure)

        for label, path in QUICK_NAV_SHORTCUTS:
            self._add_button(label, path)

    def _add_button(self, label: str, path: str) -> None:
        """Add a single shortcut button."""
        btn = ttk.Button(
            self._btn_frame,
            text=label,
            command=lambda p=path: self._navigate(p),
            padding=(8, 4),
        )
        btn.pack(side=tk.LEFT, padx=3, pady=2)
        Tooltip(btn, path)

    def _navigate(self, path: str) -> None:
        """Expand ``~`` and invoke the navigate callback."""
        import os
        expanded = os.path.expanduser(path) if "~" in path else path
        logger.info("Quick-nav → %s", expanded)
        self._on_navigate(expanded)

    def _on_frame_configure(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        """Update the canvas scroll region when the button frame resizes."""
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))
