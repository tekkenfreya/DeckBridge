"""Transfer progress dialog for DeckBridge."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk
from typing import Callable

from app.transfer import TransferItem, TransferStatus
from app.utils.path_helpers import human_readable_size

logger = logging.getLogger(__name__)

_DARK_BG = "#1b2838"
_DARK_FG = "#c7d5e0"
_DARK_ENTRY = "#263448"
_DARK_ACCENT = "#1a9fff"
_DARK_ERROR = "#e05c5c"
_DARK_SUCCESS = "#5ba85a"

_AUTO_CLOSE_DELAY_MS = 500


class TransferProgressDialog(tk.Toplevel):
    """Non-modal dialog showing per-file and overall transfer progress.

    Open with::

        dialog = TransferProgressDialog(root, total_items=3, on_cancel=queue.cancel_all)
        dialog.on_progress(item)        # call from main thread
        dialog.on_item_complete(item)   # call from main thread
    """

    def __init__(
        self,
        master: tk.Widget,
        total_items: int = 1,
        on_cancel: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        """Create the progress dialog."""
        super().__init__(master, **kwargs)
        self._total = total_items
        self._completed = 0
        self._on_cancel = on_cancel

        self.title("Transferring Files")
        self.configure(background=_DARK_BG)
        self.resizable(True, False)
        self.minsize(420, 200)
        self.grab_set()  # Focus trap (non-fully modal)

        self._build()
        self._center_on_master(master)

    def _build(self) -> None:
        """Construct the dialog widgets."""
        pad = {"padx": 16, "pady": 6}

        # Current file label
        self._file_label = ttk.Label(
            self, text="Preparing…", anchor=tk.W, wraplength=380
        )
        self._file_label.pack(fill=tk.X, **pad)

        # Per-file progress bar
        self._file_progress = ttk.Progressbar(
            self, orient=tk.HORIZONTAL, length=380, mode="determinate"
        )
        self._file_progress.pack(fill=tk.X, padx=16, pady=2)

        # Speed / ETA row
        info_row = ttk.Frame(self)
        info_row.pack(fill=tk.X, padx=16, pady=2)
        self._speed_label = ttk.Label(info_row, text="", foreground="#a8b5c2")
        self._speed_label.pack(side=tk.LEFT)
        self._eta_label = ttk.Label(info_row, text="", foreground="#a8b5c2")
        self._eta_label.pack(side=tk.RIGHT)

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)

        # Overall progress
        ttk.Label(self, text="Overall progress:", anchor=tk.W).pack(
            fill=tk.X, padx=16, pady=(0, 2)
        )
        self._overall_progress = ttk.Progressbar(
            self, orient=tk.HORIZONTAL, length=380, mode="determinate",
            maximum=max(1, self._total)
        )
        self._overall_progress.pack(fill=tk.X, padx=16, pady=2)
        self._overall_label = ttk.Label(
            self, text=f"0 / {self._total} files", foreground="#a8b5c2"
        )
        self._overall_label.pack(anchor=tk.W, padx=16)

        # Cancel button
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, padx=16, pady=12)
        self._cancel_btn = ttk.Button(btn_frame, text="Cancel", command=self._on_cancel_clicked)
        self._cancel_btn.pack(side=tk.RIGHT)

    # ------------------------------------------------------------------
    # Public callbacks (must be called from the main thread)
    # ------------------------------------------------------------------

    def on_progress(self, item: TransferItem) -> None:
        """Update per-file progress for *item*."""
        name = item.source_path.split("/")[-1].split("\\")[-1]
        direction = "Uploading" if item.direction.name == "UPLOAD" else "Downloading"
        self._file_label.configure(text=f"{direction}: {name}")

        pct = item.progress_fraction * 100
        self._file_progress.configure(value=pct)

        speed = item.speed_mbps
        if speed > 0:
            self._speed_label.configure(text=f"{speed:.1f} MB/s")
        else:
            self._speed_label.configure(text="")

        eta = item.eta_seconds
        if eta is not None:
            if eta < 60:
                self._eta_label.configure(text=f"ETA: {int(eta)}s")
            else:
                self._eta_label.configure(text=f"ETA: {int(eta // 60)}m {int(eta % 60)}s")
        else:
            self._eta_label.configure(text="")

    def on_item_complete(self, item: TransferItem) -> None:
        """Increment the completed counter; auto-close when queue is empty."""
        self._completed += 1
        self._overall_progress.configure(value=self._completed)
        self._overall_label.configure(
            text=f"{self._completed} / {self._total} files"
        )

        if item.status == TransferStatus.FAILED:
            self._file_label.configure(
                text=f"Failed: {item.error or 'Unknown error'}",
            )
        elif item.status == TransferStatus.CANCELLED:
            self._file_label.configure(text="Cancelled.")

        if self._completed >= self._total:
            self._file_label.configure(text="All transfers complete.")
            self._cancel_btn.configure(state=tk.DISABLED)
            self.after(_AUTO_CLOSE_DELAY_MS, self._close)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_cancel_clicked(self) -> None:
        """Invoke the cancel callback and close the dialog."""
        if self._on_cancel:
            self._on_cancel()
        self._close()

    def _close(self) -> None:
        """Destroy this dialog."""
        try:
            self.grab_release()
            self.destroy()
        except tk.TclError:
            pass

    def _center_on_master(self, master: tk.Widget) -> None:
        """Position the dialog near the centre of *master*."""
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        mx = master.winfo_rootx() + master.winfo_width() // 2
        my = master.winfo_rooty() + master.winfo_height() // 2
        self.geometry(f"+{mx - w // 2}+{my - h // 2}")
