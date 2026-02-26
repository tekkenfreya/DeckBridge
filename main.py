"""DeckBridge — entry point.

Configures logging, applies the dark ttk theme, creates the root window,
and starts the Tkinter main loop.
"""

from __future__ import annotations

import logging
import sys
import tkinter as tk
from tkinter import ttk

try:
    from tkinterdnd2 import TkinterDnD
    _DND_AVAILABLE = True
except ImportError:
    TkinterDnD = None  # type: ignore[assignment,misc]
    _DND_AVAILABLE = False

from app import App

_LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
_DATE_FORMAT = "%H:%M:%S"

_DARK_BG = "#1b2838"
_DARK_FG = "#c7d5e0"
_DARK_SELECT = "#2a475e"
_DARK_ACCENT = "#1a9fff"
_DARK_BUTTON = "#2a3f5f"
_DARK_ENTRY = "#263448"
_DARK_BORDER = "#374e6a"

MIN_WIDTH = 900
MIN_HEIGHT = 600
DEFAULT_WIDTH = 1200
DEFAULT_HEIGHT = 750


def _configure_logging() -> None:
    """Set up root logging to stderr."""
    logging.basicConfig(
        level=logging.DEBUG,
        format=_LOG_FORMAT,
        datefmt=_DATE_FORMAT,
        stream=sys.stderr,
    )
    # Quieten noisy third-party loggers
    logging.getLogger("paramiko").setLevel(logging.WARNING)


def _apply_dark_theme(root: tk.Tk) -> None:
    """Apply a Steam Deck-inspired dark theme using ttk.Style."""
    style = ttk.Style(root)
    style.theme_use("clam")

    # General widget defaults
    style.configure(
        ".",
        background=_DARK_BG,
        foreground=_DARK_FG,
        fieldbackground=_DARK_ENTRY,
        bordercolor=_DARK_BORDER,
        darkcolor=_DARK_BG,
        lightcolor=_DARK_BG,
        troughcolor=_DARK_BG,
        selectbackground=_DARK_SELECT,
        selectforeground=_DARK_FG,
        insertcolor=_DARK_FG,
        relief="flat",
        font=("TkDefaultFont", 11),
    )

    # Frame
    style.configure("TFrame", background=_DARK_BG)
    style.configure("TLabelframe", background=_DARK_BG, foreground=_DARK_FG)
    style.configure("TLabelframe.Label", background=_DARK_BG, foreground=_DARK_FG)

    # Label
    style.configure("TLabel", background=_DARK_BG, foreground=_DARK_FG)

    # Button
    style.configure(
        "TButton",
        background=_DARK_BUTTON,
        foreground=_DARK_FG,
        bordercolor=_DARK_BORDER,
        focuscolor=_DARK_ACCENT,
        padding=(8, 4),
    )
    style.map(
        "TButton",
        background=[("active", _DARK_SELECT), ("pressed", _DARK_ACCENT)],
        foreground=[("active", "#ffffff")],
    )

    # Accent button (primary actions)
    style.configure(
        "Accent.TButton",
        background=_DARK_ACCENT,
        foreground="#ffffff",
        bordercolor=_DARK_ACCENT,
        padding=(10, 5),
    )
    style.map(
        "Accent.TButton",
        background=[("active", "#1488cc"), ("pressed", "#0f6fa3")],
    )

    # Entry
    style.configure(
        "TEntry",
        fieldbackground=_DARK_ENTRY,
        foreground=_DARK_FG,
        bordercolor=_DARK_BORDER,
        insertcolor=_DARK_FG,
    )

    # Combobox
    style.configure(
        "TCombobox",
        fieldbackground=_DARK_ENTRY,
        foreground=_DARK_FG,
        selectbackground=_DARK_SELECT,
        bordercolor=_DARK_BORDER,
    )
    style.map("TCombobox", fieldbackground=[("readonly", _DARK_ENTRY)])

    # Notebook
    style.configure(
        "TNotebook",
        background=_DARK_BG,
        bordercolor=_DARK_BORDER,
        tabmargins=[2, 2, 2, 0],
    )
    style.configure(
        "TNotebook.Tab",
        background=_DARK_BUTTON,
        foreground=_DARK_FG,
        padding=[10, 4],
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", _DARK_SELECT)],
        foreground=[("selected", "#ffffff")],
    )

    # Treeview
    style.configure(
        "Treeview",
        background=_DARK_ENTRY,
        foreground=_DARK_FG,
        fieldbackground=_DARK_ENTRY,
        bordercolor=_DARK_BORDER,
        rowheight=24,
    )
    style.configure(
        "Treeview.Heading",
        background=_DARK_BUTTON,
        foreground=_DARK_FG,
        relief="flat",
        bordercolor=_DARK_BORDER,
    )
    style.map(
        "Treeview",
        background=[("selected", _DARK_SELECT)],
        foreground=[("selected", "#ffffff")],
    )
    style.map(
        "Treeview.Heading",
        background=[("active", _DARK_SELECT)],
    )

    # Scrollbar
    style.configure(
        "TScrollbar",
        background=_DARK_BUTTON,
        troughcolor=_DARK_BG,
        bordercolor=_DARK_BORDER,
        arrowcolor=_DARK_FG,
    )

    # Progressbar
    style.configure(
        "TProgressbar",
        background=_DARK_ACCENT,
        troughcolor=_DARK_BG,
        bordercolor=_DARK_BORDER,
    )

    # Separator
    style.configure("TSeparator", background=_DARK_BORDER)

    # Scale
    style.configure(
        "TScale",
        background=_DARK_BG,
        troughcolor=_DARK_ENTRY,
        bordercolor=_DARK_BORDER,
    )

    # Root window background
    root.configure(background=_DARK_BG)


def main() -> None:
    """Bootstrap and run DeckBridge."""
    _configure_logging()
    log = logging.getLogger(__name__)
    log.info("Starting DeckBridge")

    if _DND_AVAILABLE:
        root = TkinterDnD.Tk()
        log.debug("TkinterDnD root window created")
    else:
        root = tk.Tk()
        log.warning("tkinterdnd2 not available — drag-and-drop disabled")
    root.title("DeckBridge")

    # Set window / taskbar icon (works in both dev and frozen EXE via image_loader)
    from app.utils import image_loader as _img_loader
    _icon_photo = _img_loader.get("app_icon", size=256)
    if _icon_photo is not None:
        root.iconphoto(True, _icon_photo)
    else:
        log.warning("App icon not found — window will use default icon")

    root.minsize(MIN_WIDTH, MIN_HEIGHT)
    root.geometry(f"{DEFAULT_WIDTH}x{DEFAULT_HEIGHT}")

    _apply_dark_theme(root)

    app = App(root)  # noqa: F841

    log.info("Entering main loop")
    root.mainloop()


if __name__ == "__main__":
    main()
