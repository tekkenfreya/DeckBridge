"""Main dual-pane file browser window for DeckBridge."""

from __future__ import annotations

import logging
import os
import tkinter as tk
from tkinter import ttk
from typing import Optional

from app.config import ConfigManager
from app.connection import ConnectionState, SSHConnection
from app.ui.components import StatusBar, Tooltip
from app.ui.pane import FilePane
from app.ui.toolbar import QuickNavToolbar

logger = logging.getLogger(__name__)

_DARK_BG = "#1b2838"
_DARK_FG = "#c7d5e0"
_DARK_ACCENT = "#1a9fff"
_DARK_BORDER = "#374e6a"

_STATE_COLORS: dict[ConnectionState, str] = {
    ConnectionState.DISCONNECTED: "#808080",
    ConnectionState.CONNECTING: "#f5a623",
    ConnectionState.CONNECTED: "#5ba85a",
    ConnectionState.RECONNECTING: "#f5a623",
    ConnectionState.ERROR: "#e05c5c",
}

_STATE_LABELS: dict[ConnectionState, str] = {
    ConnectionState.DISCONNECTED: "Disconnected",
    ConnectionState.CONNECTING: "Connecting…",
    ConnectionState.CONNECTED: "Connected",
    ConnectionState.RECONNECTING: "Reconnecting…",
    ConnectionState.ERROR: "Error",
}


# ---------------------------------------------------------------------------
# Connection indicator
# ---------------------------------------------------------------------------


class ConnectionIndicator(ttk.Frame):
    """A small colored dot + label that reflects the current connection state."""

    def __init__(self, master: tk.Widget, **kwargs) -> None:
        """Create the indicator in disconnected state."""
        super().__init__(master, **kwargs)
        self._canvas = tk.Canvas(
            self,
            width=12,
            height=12,
            background=_DARK_BG,
            highlightthickness=0,
        )
        self._canvas.pack(side=tk.LEFT, padx=(0, 4))
        self._dot = self._canvas.create_oval(2, 2, 10, 10, fill="#808080", outline="")

        self._label = ttk.Label(self, text="Disconnected")
        self._label.pack(side=tk.LEFT)

        self.update_state(ConnectionState.DISCONNECTED)

    def update_state(self, state: ConnectionState, message: str | None = None) -> None:
        """Update the indicator color and label to reflect *state*."""
        color = _STATE_COLORS.get(state, "#808080")
        label = _STATE_LABELS.get(state, state.name)
        self._canvas.itemconfigure(self._dot, fill=color)
        self._label.configure(text=label)
        if message:
            Tooltip(self._label, message)


# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------


class MainWindow(ttk.Frame):
    """The main dual-pane file browser.

    Left pane shows local files; right pane shows remote Steam Deck files.
    The toolbar above the right pane provides quick-navigate shortcuts.
    """

    def __init__(self, master: tk.Widget, config: ConfigManager | None = None, **kwargs) -> None:
        """Build the main window layout."""
        super().__init__(master, **kwargs)
        self._config = config
        self._connection: SSHConnection | None = None
        self._active_profile_name: str | None = None

        self._build_layout()

    def _build_layout(self) -> None:
        """Construct the full layout: header, toolbar, paned browser, status bar."""
        # ---- Header ----
        header = ttk.Frame(self, padding=(8, 6))
        header.pack(fill=tk.X, side=tk.TOP)

        ttk.Label(
            header,
            text="DeckBridge",
            font=("TkDefaultFont", 14, "bold"),
            foreground=_DARK_ACCENT,
        ).pack(side=tk.LEFT)

        # Connection controls (right side)
        ctrl_frame = ttk.Frame(header)
        ctrl_frame.pack(side=tk.RIGHT)

        self._indicator = ConnectionIndicator(ctrl_frame)
        self._indicator.pack(side=tk.LEFT, padx=(0, 12))

        self._profile_var = tk.StringVar()
        self._profile_combo = ttk.Combobox(
            ctrl_frame,
            textvariable=self._profile_var,
            state="readonly",
            width=26,
        )
        self._profile_combo.pack(side=tk.LEFT, padx=4)
        Tooltip(self._profile_combo, "Select a connection profile")

        self._connect_btn = ttk.Button(
            ctrl_frame,
            text="Connect",
            style="Accent.TButton",
            command=self._on_connect_clicked,
        )
        self._connect_btn.pack(side=tk.LEFT, padx=4)
        Tooltip(self._connect_btn, "Connect to your Steam Deck")

        self._disconnect_btn = ttk.Button(
            ctrl_frame,
            text="Disconnect",
            command=self._on_disconnect_clicked,
            state=tk.DISABLED,
        )
        self._disconnect_btn.pack(side=tk.LEFT, padx=4)
        Tooltip(self._disconnect_btn, "Disconnect from Steam Deck")

        self._manage_btn = ttk.Button(
            ctrl_frame,
            text="Manage",
            command=self._on_manage_clicked,
        )
        self._manage_btn.pack(side=tk.LEFT, padx=4)
        Tooltip(self._manage_btn, "Add, edit or delete connection profiles")

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X)

        # ---- Quick-nav toolbar ----
        self._toolbar = QuickNavToolbar(
            self,
            on_navigate=self._navigate_remote,
            padding=(4, 2),
        )
        self._toolbar.pack(fill=tk.X)

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X)

        # ---- Status bar ----
        self._status_bar = StatusBar(self)
        self._status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # ---- Dual-pane browser ----
        local_start = (
            self._config.get("local_start_path") if self._config else os.path.expanduser("~")
        )
        remote_start = (
            self._config.get("remote_start_path") if self._config else "/home/deck"
        )

        browser_frame = ttk.Frame(self)
        browser_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        browser_frame.columnconfigure(0, weight=1)
        browser_frame.columnconfigure(1, weight=0)   # fixed-width transfer strip
        browser_frame.columnconfigure(2, weight=1)
        browser_frame.rowconfigure(0, weight=1)

        self._local_pane = FilePane(
            browser_frame,
            title="Local PC",
            connection=None,
            start_path=local_start,
            on_status=self._status_bar.set,
            on_copy_out=self._copy_local_to_remote,
        )
        self._local_pane.grid(row=0, column=0, sticky="nsew")

        transfer_strip = self._build_transfer_strip(browser_frame)
        transfer_strip.grid(row=0, column=1, sticky="ns", padx=6)

        self._remote_pane = FilePane(
            browser_frame,
            title="Steam Deck",
            connection=None,
            start_path="",
            on_status=self._status_bar.set,
            on_copy_out=self._copy_remote_to_local,
        )
        self._remote_pane.grid(row=0, column=2, sticky="nsew")

        self._refresh_profile_list()
        self._status_bar.set("Ready — connect to your Steam Deck to browse files.")

        # Register DnD targets after the widget tree is built
        self.after(0, self._register_dnd)

    # ------------------------------------------------------------------
    # Transfer strip
    # ------------------------------------------------------------------

    def _build_transfer_strip(self, master: tk.Widget) -> ttk.Frame:
        """Build the vertical button strip between the two panes."""
        frame = ttk.Frame(master)

        # Spacer to push buttons toward vertical center
        ttk.Frame(frame).pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self._copy_right_btn = ttk.Button(
            frame,
            text="→ Copy",
            command=self._copy_local_to_remote,
            state=tk.DISABLED,
            width=8,
        )
        self._copy_right_btn.pack(side=tk.TOP, pady=4)
        Tooltip(self._copy_right_btn, "Copy selected PC files to Steam Deck")

        self._copy_left_btn = ttk.Button(
            frame,
            text="← Copy",
            command=self._copy_remote_to_local,
            state=tk.DISABLED,
            width=8,
        )
        self._copy_left_btn.pack(side=tk.TOP, pady=4)
        Tooltip(self._copy_left_btn, "Copy selected Steam Deck files to PC")

        # Bottom spacer
        ttk.Frame(frame).pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        return frame

    def _copy_local_to_remote(self, paths: list[str] | None = None) -> None:
        """Copy selected local files to the remote pane's current directory."""
        selected = paths or self._local_pane.get_selected_paths()
        if not selected:
            self._status_bar.set("Select files in the PC pane first.")
            return
        dest = self._remote_pane.current_path or "/home/deck"
        self._start_transfers(selected, dest, upload=True)

    def _copy_remote_to_local(self, paths: list[str] | None = None) -> None:
        """Copy selected remote files to the local pane's current directory."""
        selected = paths or self._remote_pane.get_selected_paths()
        if not selected:
            self._status_bar.set("Select files in the Steam Deck pane first.")
            return
        dest = self._local_pane.current_path or os.path.expanduser("~")
        self._start_transfers(selected, dest, upload=False)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _navigate_remote(self, path: str) -> None:
        """Navigate the remote pane to *path*."""
        if self._connection and self._connection.state == ConnectionState.CONNECTED:
            self._remote_pane.navigate_to(path)
        else:
            self._status_bar.set("Not connected — connect to your Steam Deck first.")

    # ------------------------------------------------------------------
    # Connection wiring
    # ------------------------------------------------------------------

    def set_connection(self, connection: SSHConnection) -> None:
        """Wire a live SSHConnection into the remote pane."""
        self._connection = connection
        self._remote_pane.set_connection(connection)
        # Subscribe to state changes
        original_cb = connection._on_state_change

        def wrapped_cb(state: ConnectionState, message: str | None) -> None:
            self.after(0, self._on_connection_state_change, state, message)
            if original_cb:
                original_cb(state, message)

        connection._on_state_change = wrapped_cb

        # Reflect current state immediately — this also triggers remote pane
        # navigation if the connection is already CONNECTED (wizard flow).
        self._on_connection_state_change(connection.state, None)

    def _on_connection_state_change(
        self, state: ConnectionState, message: str | None
    ) -> None:
        """Update the indicator, button states, and remote pane (main thread)."""
        self._indicator.update_state(state, message)
        connected = state == ConnectionState.CONNECTED
        self._connect_btn.configure(
            state=tk.DISABLED if connected else tk.NORMAL
        )
        self._disconnect_btn.configure(
            state=tk.NORMAL if connected else tk.DISABLED
        )
        transfer_state = tk.NORMAL if connected else tk.DISABLED
        self._copy_right_btn.configure(state=transfer_state)
        self._copy_left_btn.configure(state=transfer_state)
        if not connected:
            self._active_profile_name = None
        if message:
            self._status_bar.set(message)

        # Navigate the remote pane whenever we reach CONNECTED state —
        # this covers both the initial wizard flow and manual reconnects.
        if connected:
            remote_start = (
                self._config.get("remote_start_path") if self._config else "/home/deck"
            )
            self._status_bar.set(f"Connected — loading {remote_start}")
            self._remote_pane.navigate_to(remote_start)

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def auto_connect(self) -> None:
        """Connect automatically using the most recently saved profile (called on startup)."""
        self._on_connect_clicked()

    def _refresh_profile_list(self) -> None:
        """Repopulate the profile Combobox from the config store."""
        profiles = self._config.get_profiles() if self._config else []
        names = [p.get("name", "?") for p in profiles]
        self._profile_combo["values"] = names
        if names:
            if self._profile_var.get() not in names:
                self._profile_var.set(names[-1])
        else:
            self._profile_var.set("")

    def _on_manage_clicked(self) -> None:
        """Open the Profile Manager dialog."""
        from app.ui.profiles import ProfileManagerDialog

        ProfileManagerDialog(
            self,
            config=self._config,
            on_profiles_changed=self._refresh_profile_list,
            active_profile_name=self._active_profile_name,
            on_disconnect=self._on_disconnect_clicked,
        )

    def _on_connect_clicked(self) -> None:
        """Connect (or reconnect) using the selected profile."""
        name = self._profile_var.get()
        profile = self._config.get_profile(name) if name and self._config else None
        if not profile:
            profiles = self._config.get_profiles() if self._config else []
            profile = profiles[-1] if profiles else None
        if not profile:
            self._status_bar.set("No profiles — click Manage to add one.")
            return

        # If already connecting or connected, do nothing
        if self._connection and self._connection.state in (
            ConnectionState.CONNECTING, ConnectionState.CONNECTED
        ):
            return

        self._active_profile_name = profile.get("name")
        from app.connection import SSHConnection
        conn = SSHConnection(
            host=profile.get("host", ""),
            port=profile.get("port", 22),
            username=profile.get("username", "deck"),
            auth_type=profile.get("auth_type", "password"),
            key_path=profile.get("key_path"),
        )
        self.set_connection(conn)

        import threading
        host = profile.get("host", "")
        self._status_bar.set(f"Connecting to {host}…")
        self._connect_btn.configure(state=tk.DISABLED)
        t = threading.Thread(target=self._connect_worker, args=(conn,), daemon=True)
        t.start()

    def _connect_worker(self, conn: SSHConnection) -> None:
        """Background thread: attempt connection and report to UI."""
        import paramiko, socket as _socket
        from app.connection import UnknownHostError
        try:
            conn.connect()
        except UnknownHostError as exc:
            self.after(0, self._status_bar.set,
                       f"Unknown host key for {conn.host} — re-run the wizard to verify.")
        except paramiko.AuthenticationException:
            self.after(0, self._status_bar.set,
                       "Authentication failed — check your password.")
        except (_socket.timeout, TimeoutError):
            self.after(0, self._status_bar.set,
                       "Connection timed out — is the Steam Deck on the same network?")
        except OSError as exc:
            self.after(0, self._status_bar.set, f"Network error: {exc}")

    def _on_disconnect_clicked(self) -> None:
        """Disconnect from the Steam Deck."""
        if self._connection:
            import threading
            t = threading.Thread(target=self._connection.disconnect, daemon=True)
            t.start()

    # ------------------------------------------------------------------
    # DnD hooks (populated in Phase 7)
    # ------------------------------------------------------------------

    def _register_dnd(self) -> None:
        """Register drag-and-drop targets on both panes (requires tkinterdnd2)."""
        try:
            from tkinterdnd2 import DND_FILES
            self._remote_pane._tree.drop_target_register(DND_FILES)
            self._remote_pane._tree.dnd_bind("<<Drop>>", self._on_drop_to_remote)
            self._local_pane._tree.drop_target_register(DND_FILES)
            self._local_pane._tree.dnd_bind("<<Drop>>", self._on_drop_to_local)
            logger.debug("DnD targets registered")
        except ImportError:
            logger.debug("tkinterdnd2 not available — skipping DnD registration")
        except Exception:
            logger.exception("Failed to register DnD targets")

    def _on_drop_to_remote(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        """Handle a drag-and-drop onto the remote pane — upload."""
        if self._connection is None or self._connection.state != ConnectionState.CONNECTED:
            self._status_bar.set("Not connected — cannot upload files.")
            return

        paths = self._parse_dnd_paths(event.data)
        if not paths:
            return

        remote_dir = self._remote_pane.current_path
        if not remote_dir:
            remote_dir = "/home/deck"

        self._start_transfers(paths, remote_dir, upload=True)

    def _on_drop_to_local(self, event: tk.Event) -> None:  # type: ignore[type-arg]
        """Handle a drag-and-drop onto the local pane — download."""
        if self._connection is None or self._connection.state != ConnectionState.CONNECTED:
            self._status_bar.set("Not connected — cannot download files.")
            return

        paths = self._parse_dnd_paths(event.data)
        if not paths:
            return

        local_dir = self._local_pane.current_path
        if not local_dir:
            import os
            local_dir = os.path.expanduser("~")

        self._start_transfers(paths, local_dir, upload=False)

    @staticmethod
    def _parse_dnd_paths(data: str) -> list[str]:
        """Parse the DnD event data into a list of file paths."""
        import re
        # tkinterdnd2 returns space-separated paths, braces-quoted if spaces in name
        paths = re.findall(r'\{([^}]+)\}|(\S+)', data)
        result = []
        for braced, plain in paths:
            p = braced or plain
            if p:
                result.append(p)
        return result

    def _start_transfers(self, paths: list[str], dest_dir: str, upload: bool) -> None:
        """Enqueue transfers and open the progress dialog."""
        from app.transfer import TransferDirection, TransferQueue
        from app.ui.progress import TransferProgressDialog
        import os

        direction = TransferDirection.UPLOAD if upload else TransferDirection.DOWNLOAD

        def overwrite_prompt(path: str) -> bool:
            from tkinter import messagebox
            return messagebox.askyesno(
                "Overwrite?",
                f"'{os.path.basename(path)}' already exists. Overwrite?",
                parent=self,
            )

        tq = TransferQueue(
            connection=self._connection,
            on_overwrite_prompt=overwrite_prompt,
        )

        items = []
        for src in paths:
            name = os.path.basename(src.rstrip("/\\"))
            if upload:
                dest = f"{dest_dir.rstrip('/')}/{name}"
            else:
                dest = os.path.join(dest_dir, name)
            item = tq.enqueue(src, dest, direction)
            items.append(item)

        dialog = TransferProgressDialog(
            self,
            total_items=len(items),
            on_cancel=tq.cancel_all,
        )

        def _on_progress(item):
            self.after(0, dialog.on_progress, item)

        def _on_complete(item):
            self.after(0, dialog.on_item_complete, item)
            # Refresh the destination pane
            if upload:
                self.after(0, self._remote_pane.navigate_to, self._remote_pane.current_path)
            else:
                self.after(0, self._local_pane.navigate_to, self._local_pane.current_path)

        tq.on_progress = _on_progress
        tq.on_item_complete = _on_complete
