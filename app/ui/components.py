"""Shared reusable UI components for DeckBridge."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

_DARK_BG = "#1b2838"
_DARK_FG = "#c7d5e0"
_DARK_ENTRY = "#263448"
_DARK_BORDER = "#374e6a"
_DARK_ACCENT = "#1a9fff"

# Braille spinner frames for async spinners
_SPINNER_FRAMES = ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"]


class StatusBar(ttk.Frame):
    """Persistent status bar displayed at the bottom of the main window."""

    def __init__(self, master: tk.Widget, **kwargs) -> None:
        """Create the status bar with an initial ready message."""
        super().__init__(master, **kwargs)
        self._var = tk.StringVar(value="Ready")
        self._label = ttk.Label(
            self,
            textvariable=self._var,
            anchor=tk.W,
            padding=(6, 2),
        )
        self._label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(side=tk.TOP, fill=tk.X)

    def set(self, message: str) -> None:
        """Update the status bar text."""
        self._var.set(message)


class Tooltip:
    """Show a tooltip near a widget when the user hovers over it."""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        """Attach hover handlers to *widget*."""
        self._widget = widget
        self._text = text
        self._tip_window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        """Display the tooltip near the cursor."""
        if self._tip_window:
            return
        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.configure(background=_DARK_BORDER)
        label = tk.Label(
            tw,
            text=self._text,
            background=_DARK_ENTRY,
            foreground=_DARK_FG,
            relief=tk.FLAT,
            padx=6,
            pady=3,
            font=("TkDefaultFont", 10),
        )
        label.pack()
        self._tip_window = tw

    def _hide(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        """Destroy the tooltip window."""
        if self._tip_window:
            self._tip_window.destroy()
            self._tip_window = None


class CopyableText(ttk.Frame):
    """A read-only Text widget with a Copy button below it."""

    def __init__(self, master: tk.Widget, text: str = "", height: int = 3, **kwargs) -> None:
        """Create the widget with initial *text*."""
        super().__init__(master, **kwargs)
        self._text_widget = tk.Text(
            self,
            height=height,
            background=_DARK_ENTRY,
            foreground=_DARK_FG,
            insertbackground=_DARK_FG,
            relief=tk.FLAT,
            font=("Courier", 11),
            wrap=tk.NONE,
            state=tk.DISABLED,
            padx=8,
            pady=4,
        )
        self._text_widget.pack(fill=tk.BOTH, expand=True)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, pady=(2, 0))
        self._copy_btn = ttk.Button(btn_frame, text="Copy", command=self._copy)
        self._copy_btn.pack(side=tk.RIGHT)
        Tooltip(self._copy_btn, "Copy to clipboard")

        if text:
            self.set_text(text)

    def set_text(self, text: str) -> None:
        """Replace the displayed text."""
        self._text_widget.configure(state=tk.NORMAL)
        self._text_widget.delete("1.0", tk.END)
        self._text_widget.insert("1.0", text)
        self._text_widget.configure(state=tk.DISABLED)

    def _copy(self) -> None:
        """Copy the text content to the system clipboard."""
        content = self._text_widget.get("1.0", tk.END).strip()
        self._text_widget.clipboard_clear()
        self._text_widget.clipboard_append(content)


class SpinnerLabel(ttk.Label):
    """A label that animates through braille spinner frames.

    Usage::

        spinner = SpinnerLabel(parent, text="")
        spinner.start()
        # ... async work ...
        spinner.stop()
    """

    _INTERVAL_MS = 80

    def __init__(self, master: tk.Widget, **kwargs) -> None:
        """Initialise the spinner (not running)."""
        super().__init__(master, **kwargs)
        self._running = False
        self._frame_idx = 0
        self._after_id: str | None = None

    def start(self) -> None:
        """Begin the spinner animation."""
        if self._running:
            return
        self._running = True
        self._tick()

    def stop(self, final_text: str = "") -> None:
        """Halt the spinner and optionally set *final_text*."""
        self._running = False
        if self._after_id:
            self.after_cancel(self._after_id)
            self._after_id = None
        self.configure(text=final_text)

    def _tick(self) -> None:
        """Advance one frame."""
        if not self._running:
            return
        self.configure(text=_SPINNER_FRAMES[self._frame_idx % len(_SPINNER_FRAMES)])
        self._frame_idx += 1
        self._after_id = self.after(self._INTERVAL_MS, self._tick)
