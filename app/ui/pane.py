"""Dual-pane file browser widgets for DeckBridge.

Each pane shows a directory listing (local or remote) with a breadcrumb
navigation bar, sortable columns, and a spinner during background loads.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import messagebox, simpledialog, ttk
from typing import Callable, Optional

from app.ui.components import SpinnerLabel, Tooltip
from app.utils.path_helpers import (
    DRIVES_ROOT,
    get_path_segments,
    human_readable_size,
    validate_remote_path,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level clipboard — shared between all FilePane instances so that
# "cut here, paste there" works across both panes.
# ---------------------------------------------------------------------------
_clipboard: dict = {
    "mode": None,        # "cut" | None
    "paths": [],         # list[str] of full source paths
    "is_remote": False,  # True if the source was a remote pane
    "connection": None,  # source SSHConnection object (for identity checks)
}

_DARK_BG = "#1b2838"
_DARK_FG = "#c7d5e0"
_DARK_ENTRY = "#263448"
_DARK_BORDER = "#374e6a"
_DARK_ACCENT = "#1a9fff"
_DARK_FOLDER = "#f5a623"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class FileEntry:
    """Represents a single file or directory in a listing."""

    name: str
    size: int  # bytes; 0 for directories
    modified: float  # epoch timestamp
    is_dir: bool
    is_hidden: bool = field(default=False)

    @property
    def size_str(self) -> str:
        """Human-readable file size."""
        return "—" if self.is_dir else human_readable_size(self.size)

    @property
    def modified_str(self) -> str:
        """Formatted modification time."""
        import datetime
        try:
            return datetime.datetime.fromtimestamp(self.modified).strftime("%Y-%m-%d %H:%M")
        except (OSError, OverflowError, ValueError):
            return "—"


# ---------------------------------------------------------------------------
# Breadcrumb bar
# ---------------------------------------------------------------------------


class BreadcrumbBar(ttk.Frame):
    """Horizontally scrollable clickable breadcrumb path bar."""

    def __init__(
        self,
        master: tk.Widget,
        on_navigate: Callable[[str], None],
        **kwargs,
    ) -> None:
        """Create the breadcrumb bar."""
        super().__init__(master, **kwargs)
        self._on_navigate = on_navigate

        self._canvas = tk.Canvas(
            self,
            background=_DARK_BG,
            height=28,
            highlightthickness=0,
        )
        self._scrollbar = ttk.Scrollbar(
            self, orient=tk.HORIZONTAL, command=self._canvas.xview
        )
        self._canvas.configure(xscrollcommand=self._scrollbar.set)

        self._inner = ttk.Frame(self._canvas)
        self._inner_id = self._canvas.create_window(0, 0, anchor=tk.NW, window=self._inner)

        self._canvas.pack(side=tk.TOP, fill=tk.X, expand=True)
        self._scrollbar.pack(side=tk.BOTTOM, fill=tk.X)

        self._inner.bind("<Configure>", self._on_inner_configure)

    def _on_inner_configure(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        """Update scroll region when inner frame changes size."""
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def set_path(self, path: str) -> None:
        """Rebuild the breadcrumb buttons for *path*."""
        for child in self._inner.winfo_children():
            child.destroy()

        segments = get_path_segments(path)
        for i, (label, full_path) in enumerate(segments):
            btn = ttk.Button(
                self._inner,
                text=label,
                command=lambda p=full_path: self._on_navigate(p),
                padding=(4, 0),
            )
            btn.pack(side=tk.LEFT, padx=1)
            Tooltip(btn, full_path)
            if i < len(segments) - 1:
                ttk.Label(self._inner, text="›", foreground=_DARK_BORDER).pack(
                    side=tk.LEFT
                )

        # Scroll to the rightmost segment
        self._inner.update_idletasks()
        self._canvas.xview_moveto(1.0)


# ---------------------------------------------------------------------------
# FilePane
# ---------------------------------------------------------------------------


class FilePane(ttk.Frame):
    """A single file-browser pane (local or remote).

    Set ``connection`` to an ``SSHConnection`` instance to put the pane in
    remote mode; leave it ``None`` for local mode.
    """

    _COLUMNS = ("name", "size", "modified")
    _COL_WIDTHS = {"name": 220, "size": 80, "modified": 130}
    _COL_HEADINGS = {"name": "Name", "size": "Size", "modified": "Modified"}

    def __init__(
        self,
        master: tk.Widget,
        title: str = "Files",
        connection=None,
        start_path: str = "",
        on_status: Callable[[str], None] | None = None,
        on_copy_out: Callable[[list[str]], None] | None = None,
        **kwargs,
    ) -> None:
        """Create the file pane."""
        super().__init__(master, **kwargs)
        self._connection = connection
        self._on_status = on_status
        self._on_copy_out = on_copy_out
        self._current_path = ""
        self._entries: list[FileEntry] = []
        self._show_hidden = False
        self._sort_column = "name"
        self._sort_reverse = False
        self._load_lock = threading.Lock()

        self._build_ui(title)

        if start_path:
            self.navigate_to(start_path)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self, title: str) -> None:
        """Build header, breadcrumb, treeview, and scrollbar."""
        # Header row
        header = ttk.Frame(self)
        header.pack(fill=tk.X, padx=4, pady=(4, 0))

        ttk.Label(header, text=title, font=("TkDefaultFont", 11, "bold")).pack(
            side=tk.LEFT
        )

        self._spinner = SpinnerLabel(
            header, text="", foreground=_DARK_ACCENT, font=("TkDefaultFont", 14)
        )
        self._spinner.pack(side=tk.RIGHT)

        self._hidden_btn = ttk.Button(
            header, text="Hidden", command=self.toggle_hidden_files, padding=(4, 2)
        )
        self._hidden_btn.pack(side=tk.RIGHT, padx=4)
        Tooltip(self._hidden_btn, "Toggle visibility of hidden (dot) files")

        self._new_folder_btn = ttk.Button(
            header, text="+ New Folder", command=self.new_folder, padding=(4, 2)
        )
        self._new_folder_btn.pack(side=tk.RIGHT, padx=4)
        Tooltip(self._new_folder_btn, "Create a new folder in the current directory")

        # Breadcrumb
        self._breadcrumb = BreadcrumbBar(self, on_navigate=self.navigate_to)
        self._breadcrumb.pack(fill=tk.X, padx=4, pady=2)

        # Treeview + scrollbar
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self._tree = ttk.Treeview(
            tree_frame,
            columns=self._COLUMNS,
            show="headings",
            selectmode="extended",
        )
        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for col in self._COLUMNS:
            self._tree.heading(
                col,
                text=self._COL_HEADINGS[col],
                command=lambda c=col: self._sort_by_column(c),
            )
            self._tree.column(col, width=self._COL_WIDTHS[col], minwidth=40)

        self._tree.bind("<Double-1>", self._on_double_click)
        self._tree.bind("<Button-3>", self._show_context_menu)
        self._tree.bind("<Button-2>", self._show_context_menu)  # macOS
        self._tree.bind("<Control-x>", lambda _e: self.cut_selected(self.get_selected_paths()))
        self._tree.bind("<Control-v>", lambda _e: self.paste_here())

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate_to(self, path: str) -> None:
        """Navigate the pane to *path*, loading contents asynchronously."""
        if self._connection is None:
            # Local pane — DRIVES_ROOT is the virtual Windows drive-list root
            self._start_load(path)
        else:
            # Remote pane — validate first
            if not validate_remote_path(path):
                self._set_status(f"Invalid remote path: {path!r}")
                return
            self._start_load(path)

    def _start_load(self, path: str) -> None:
        """Begin async loading of *path* contents."""
        self._spinner.start()
        self._tree.delete(*self._tree.get_children())
        t = threading.Thread(
            target=self._load_worker,
            args=(path,),
            daemon=True,
        )
        t.start()

    def _load_worker(self, path: str) -> None:
        """Worker thread: fetch directory contents, post result via after()."""
        try:
            if self._connection is not None:
                entries = self._fetch_remote(path)
            else:
                entries = self._fetch_local(path)
            self.after(0, self._on_load_success, path, entries)
        except PermissionError as exc:
            self.after(0, self._on_load_error, path, f"Permission denied: {exc}")
        except FileNotFoundError:
            self.after(0, self._on_load_error, path, f"Path not found: {path!r}")
        except Exception as exc:
            logger.exception("Load failed for %r", path)
            self.after(0, self._on_load_error, path, str(exc))

    def _fetch_remote(self, path: str) -> list[FileEntry]:
        """Use SFTP to list *path* on the remote host."""
        sftp = self._connection.get_sftp()
        attrs = sftp.listdir_attr(path)
        entries = []
        import stat as _stat
        for a in attrs:
            name = a.filename if hasattr(a, "filename") else str(a)
            is_dir = _stat.S_ISDIR(a.st_mode) if a.st_mode else False
            entries.append(
                FileEntry(
                    name=name,
                    size=a.st_size or 0,
                    modified=a.st_mtime or 0.0,
                    is_dir=is_dir,
                    is_hidden=name.startswith("."),
                )
            )
        return entries

    def _fetch_local(self, path: str) -> list[FileEntry]:
        """Use os.scandir (or drive enumeration) to list *path* on the local filesystem."""
        import string
        import sys

        if path == DRIVES_ROOT:
            # Windows virtual root: list every accessible drive letter
            entries = []
            for letter in string.ascii_uppercase:
                root = f"{letter}:\\"
                if os.path.exists(root):
                    try:
                        stat = os.stat(root)
                        entries.append(
                            FileEntry(
                                name=root,
                                size=0,
                                modified=stat.st_mtime,
                                is_dir=True,
                                is_hidden=False,
                            )
                        )
                    except OSError:
                        pass
            return entries

        entries = []
        with os.scandir(path) as it:
            for entry in it:
                try:
                    stat = entry.stat(follow_symlinks=False)
                    entries.append(
                        FileEntry(
                            name=entry.name,
                            size=stat.st_size,
                            modified=stat.st_mtime,
                            is_dir=entry.is_dir(follow_symlinks=False),
                            is_hidden=entry.name.startswith("."),
                        )
                    )
                except OSError:
                    pass
        return entries

    def _on_load_success(self, path: str, entries: list[FileEntry]) -> None:
        """Handle successful directory load (main thread)."""
        self._spinner.stop()
        self._current_path = path
        self._entries = entries
        self._breadcrumb.set_path(path)
        self._populate_treeview()
        self._set_status(f"Loaded {len(entries)} items from {path}")

    def _on_load_error(self, path: str, message: str) -> None:
        """Handle a load failure (main thread)."""
        self._spinner.stop()
        self._set_status(f"Error: {message}")
        logger.warning("FilePane load error for %r: %s", path, message)

    # ------------------------------------------------------------------
    # Treeview population
    # ------------------------------------------------------------------

    def _populate_treeview(self) -> None:
        """Clear and repopulate the treeview from ``_entries``."""
        self._tree.delete(*self._tree.get_children())

        visible = [
            e for e in self._entries
            if self._show_hidden or not e.is_hidden
        ]

        # Sort: directories first, then by selected column
        reverse = self._sort_reverse
        col = self._sort_column

        def sort_key(e: FileEntry):
            if col == "name":
                return (not e.is_dir, e.name.lower())
            elif col == "size":
                return (not e.is_dir, e.size)
            elif col == "modified":
                return (not e.is_dir, e.modified)
            return (not e.is_dir, e.name.lower())

        visible.sort(key=sort_key, reverse=reverse)

        from app.utils import image_loader
        folder_icon = image_loader.get("folder", 16)
        file_icon = image_loader.get("file", 16)

        for entry in visible:
            icon = folder_icon if entry.is_dir else file_icon
            values = (entry.name, entry.size_str, entry.modified_str)
            item_id = self._tree.insert(
                "",
                tk.END,
                text=entry.name,
                values=values,
                image=icon or "",
            )

    def _sort_by_column(self, column: str) -> None:
        """Sort the listing by *column*, toggling direction on repeat clicks."""
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = False
        self._populate_treeview()

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def _on_double_click(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        """Navigate into a folder on double-click."""
        item_id = self._tree.focus()
        if not item_id:
            return
        name = self._tree.item(item_id, "values")[0]
        # Find the FileEntry
        for entry in self._entries:
            if entry.name == name:
                if entry.is_dir:
                    if self._connection:
                        new_path = f"{self._current_path.rstrip('/')}/{name}"
                    elif self._current_path == DRIVES_ROOT:
                        # Entry name is the full drive root, e.g. "C:\"
                        new_path = name
                    else:
                        import os.path
                        new_path = os.path.join(self._current_path, name)
                    self.navigate_to(new_path)
                break

    def toggle_hidden_files(self) -> None:
        """Toggle visibility of hidden (dot-prefixed) files and reload the view."""
        self._show_hidden = not self._show_hidden
        self._hidden_btn.configure(
            style="Accent.TButton" if self._show_hidden else "TButton"
        )
        self._populate_treeview()

    # ------------------------------------------------------------------
    # Connection wiring
    # ------------------------------------------------------------------

    def set_connection(self, connection) -> None:
        """Wire a live SSHConnection into this pane and load the start path."""
        self._connection = connection

    def get_selected_paths(self) -> list[str]:
        """Return a list of full paths for all currently selected items."""
        paths = []
        for item_id in self._tree.selection():
            name = self._tree.item(item_id, "values")[0]
            if self._connection:
                paths.append(f"{self._current_path.rstrip('/')}/{name}")
            elif self._current_path == DRIVES_ROOT:
                # name is already a full drive root (e.g. "C:\")
                paths.append(name)
            else:
                import os.path
                paths.append(os.path.join(self._current_path, name))
        return paths

    @property
    def current_path(self) -> str:
        """The path currently displayed by this pane."""
        return self._current_path

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _show_context_menu(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        """Show a right-click context menu for the item under the cursor."""
        item_id = self._tree.identify_row(event.y)
        if item_id and item_id not in self._tree.selection():
            self._tree.selection_set(item_id)

        selected = self.get_selected_paths()
        menu = tk.Menu(self, tearoff=0)

        # New Folder is always available regardless of selection
        menu.add_command(label="New Folder", command=self.new_folder)

        # Paste is available whenever the clipboard has cut items
        paste_label = (
            f"Paste ({len(_clipboard['paths'])} item(s))"
            if _clipboard["mode"] == "cut" and _clipboard["paths"]
            else "Paste"
        )
        menu.add_command(
            label=paste_label,
            command=self.paste_here,
            state=tk.NORMAL if _clipboard["mode"] == "cut" else tk.DISABLED,
        )

        menu.add_separator()

        if selected:
            menu.add_command(
                label=f"Cut ({len(selected)} item(s))",
                command=lambda: self.cut_selected(selected),
            )

            if self._on_copy_out is not None:
                menu.add_command(
                    label="Copy to other pane",
                    command=lambda: self._on_copy_out(selected),  # type: ignore[misc]
                )

            menu.add_command(
                label="Duplicate here",
                command=lambda: self._duplicate_selected(selected),
            )

            # Rename only makes sense for a single item
            if len(selected) == 1:
                menu.add_command(
                    label="Rename",
                    command=lambda: self._rename_item(selected[0]),
                )

            menu.add_separator()

            menu.add_command(
                label=f"Delete ({len(selected)} item(s))",
                command=lambda: self.delete_selected(selected),
            )

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def cut_selected(self, paths: list[str]) -> None:
        """Mark *paths* for a move operation. Complete with :meth:`paste_here`."""
        if not paths:
            return
        _clipboard["mode"] = "cut"
        _clipboard["paths"] = list(paths)
        _clipboard["is_remote"] = self._connection is not None
        _clipboard["connection"] = self._connection
        logger.debug("Cut %d items: %s", len(paths), paths)
        self._set_status(
            f"{len(paths)} item(s) cut — navigate to destination and Paste (Ctrl+V)"
        )

    def paste_here(self) -> None:
        """Move all cut items into the current directory."""
        if _clipboard["mode"] != "cut" or not _clipboard["paths"]:
            self._set_status("Nothing to paste — cut some items first (Ctrl+X)")
            return
        if not self._current_path:
            self._set_status("Navigate to a destination folder first")
            return

        src_is_remote = _clipboard["is_remote"]
        dst_is_remote = self._connection is not None

        if src_is_remote != dst_is_remote:
            messagebox.showinfo(
                "Cross-side move",
                "Moving files between PC and Steam Deck is a transfer operation.\n\n"
                "Use 'Copy to other pane' to transfer them, then delete the originals.",
                parent=self,
            )
            return

        paths = list(_clipboard["paths"])
        # Clear clipboard before starting the thread
        _clipboard["mode"] = None
        _clipboard["paths"] = []
        _clipboard["is_remote"] = False
        _clipboard["connection"] = None

        dest_dir = self._current_path

        def _do_move() -> None:
            errors: list[str] = []
            for src in paths:
                name = os.path.basename(src.rstrip("/\\"))
                if not name:
                    continue
                try:
                    if dst_is_remote:
                        sftp = self._connection.get_sftp()
                        dst = f"{dest_dir.rstrip('/')}/{name}"
                        sftp.rename(src, dst)
                        logger.info("Moved remote: %s -> %s", src, dst)
                    else:
                        dst = os.path.join(dest_dir, name)
                        shutil.move(src, dst)
                        logger.info("Moved local: %s -> %s", src, dst)
                except Exception as exc:
                    logger.warning("Move failed for %r: %s", src, exc)
                    errors.append(f"{name}: {exc}")

            if errors:
                self.after(0, self._set_status, f"Move error: {errors[0]}")
            else:
                self.after(0, self._set_status, f"Moved {len(paths)} item(s)")
            self.after(0, self.navigate_to, dest_dir)

        threading.Thread(target=_do_move, daemon=True).start()

    def new_folder(self) -> None:
        """Prompt for a name and create a new folder in the current directory."""
        if not self._current_path:
            self._set_status("Navigate to a directory first")
            return

        name = simpledialog.askstring(
            "New Folder",
            "Folder name:",
            parent=self,
        )
        if not name or not name.strip():
            return
        name = name.strip()

        # Reject names that contain path separators
        if "/" in name or "\\" in name:
            messagebox.showerror("Invalid name", "Folder name must not contain / or \\", parent=self)
            return

        def _do_create() -> None:
            try:
                if self._connection is None:
                    new_path = os.path.join(self._current_path, name)
                    os.makedirs(new_path, exist_ok=False)
                    logger.info("Created local folder: %s", new_path)
                else:
                    sftp = self._connection.get_sftp()
                    new_path = f"{self._current_path.rstrip('/')}/{name}"
                    sftp.mkdir(new_path)
                    logger.info("Created remote folder: %s", new_path)
                self.after(0, self.navigate_to, self._current_path)
            except FileExistsError:
                self.after(0, messagebox.showerror, "Error",
                           f"'{name}' already exists", )
            except Exception as exc:
                logger.warning("New folder failed: %s", exc)
                self.after(0, self._set_status, f"New folder failed: {exc}")

        threading.Thread(target=_do_create, daemon=True).start()

    def _rename_item(self, path: str) -> None:
        """Prompt for a new name and rename *path* in-place."""
        old_name = os.path.basename(path.rstrip("/"))
        new_name = simpledialog.askstring(
            "Rename",
            "New name:",
            initialvalue=old_name,
            parent=self,
        )
        if not new_name or not new_name.strip():
            return
        new_name = new_name.strip()

        if "/" in new_name or "\\" in new_name:
            messagebox.showerror("Invalid name", "Name must not contain / or \\", parent=self)
            return

        if new_name == old_name:
            return

        def _do_rename() -> None:
            try:
                if self._connection is None:
                    parent = os.path.dirname(os.path.normpath(path))
                    new_path = os.path.join(parent, new_name)
                    os.rename(path, new_path)
                    logger.info("Renamed local: %s -> %s", path, new_path)
                else:
                    sftp = self._connection.get_sftp()
                    parent = self._current_path.rstrip("/")
                    new_path = f"{parent}/{new_name}"
                    sftp.rename(path, new_path)
                    logger.info("Renamed remote: %s -> %s", path, new_path)
                self.after(0, self.navigate_to, self._current_path)
            except Exception as exc:
                logger.warning("Rename failed: %s", exc)
                self.after(0, self._set_status, f"Rename failed: {exc}")

        threading.Thread(target=_do_rename, daemon=True).start()

    def _duplicate_selected(self, paths: list[str]) -> None:
        """Duplicate each selected item in the same directory."""
        def _do_duplicate() -> None:
            for src in paths:
                try:
                    if self._connection is None:
                        # Local duplicate
                        p = os.path.normpath(src)
                        parent = os.path.dirname(p)
                        base, ext = os.path.splitext(os.path.basename(p))
                        candidate = os.path.join(parent, f"{base}_copy{ext}")
                        n = 1
                        while os.path.exists(candidate):
                            candidate = os.path.join(parent, f"{base}_copy{n}{ext}")
                            n += 1
                        if os.path.isdir(p):
                            shutil.copytree(p, candidate)
                        else:
                            shutil.copy2(p, candidate)
                    else:
                        # Remote duplicate via SSH cp
                        src_clean = src.rstrip("/")
                        dst = f"{src_clean}_copy"
                        src_esc = src_clean.replace("'", "'\\''")
                        dst_esc = dst.replace("'", "'\\''")
                        cmd = f"cp -r '{src_esc}' '{dst_esc}'"
                        self._connection.execute_command(cmd)
                except Exception as exc:
                    logger.warning("Duplicate failed for %r: %s", src, exc)
                    self.after(
                        0,
                        self._set_status,
                        f"Duplicate failed: {exc}",
                    )

            # Refresh pane on the main thread
            if self._current_path:
                self.after(0, self.navigate_to, self._current_path)

        t = threading.Thread(target=_do_duplicate, daemon=True)
        t.start()

    def delete_selected(self, paths: list[str]) -> None:
        """Prompt the user and delete the selected items."""
        if not paths:
            return
        confirmed = messagebox.askyesno(
            "Delete?",
            f"Delete {len(paths)} item(s)? This cannot be undone.",
            parent=self,
        )
        if not confirmed:
            return

        def _do_delete() -> None:
            for p in paths:
                try:
                    if self._connection is None:
                        # Local delete
                        if os.path.isdir(p):
                            shutil.rmtree(p)
                        else:
                            os.remove(p)
                    else:
                        # Remote delete via SSH rm -rf
                        p_esc = p.replace("'", "'\\''")
                        self._connection.execute_command(f"rm -rf '{p_esc}'")
                except Exception as exc:
                    logger.warning("Delete failed for %r: %s", p, exc)
                    self.after(
                        0,
                        self._set_status,
                        f"Delete failed: {exc}",
                    )

            if self._current_path:
                self.after(0, self.navigate_to, self._current_path)

        t = threading.Thread(target=_do_delete, daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_status(self, message: str) -> None:
        """Forward a status message to the optional status callback."""
        if self._on_status:
            self._on_status(message)
